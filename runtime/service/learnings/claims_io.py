"""RFC 0005 §7.1 — v7 CLAIM file I/O for the field-transition lifecycle.

The learning lifecycle is **surfacing tiers, not directories** (RFC 0005 §7.1).
`candidate` / `note` / `principle` are one Claim at different `surfacing` levels
plus an acceptance state — not separate content types in separate dirs. So both
`promote` (query→proactive) and `dream` (proactive→always + synthesis) operate
by editing CLAIM FIELDS in place, never by moving a file between directories.

This module is the single place that knows how to:

- enumerate v7 Claim nodes (flat under the graph atomic claims tree),
- read one claim's frontmatter + body,
- transition a claim's `surfacing` tier in place (entry_id PRESERVED — it is the
  link/ledger target, never re-derived — and `content_hash` re-derived so the
  projection stays consistent),
- mint a NEW synthesized claim (`generated_by: dream`) `derived_from` its source
  claims and linked to them by `refines`/`supports` (RFC 0005 §4.3 link rel).

Writes are atomic (write .tmp → os.replace) so a power loss never leaves a
half-written claim. No LLM here: the field transitions are deterministic; the
synthesis *text* is supplied by the agent (engine stays off the generate path,
RFC 0003 / §11).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml

from ...index import parse as _parse
from ...structure import resolver as _structure
from ...util import config as _config

# The surfacing ladder (mirrors recall_v7._LADDER) — query ⊂ proactive ⊂ always.
TIER_QUERY = "query"
TIER_PROACTIVE = "proactive"
TIER_ALWAYS = "always"
_LADDER = {TIER_QUERY: 0, TIER_PROACTIVE: 1, TIER_ALWAYS: 2}


def vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def claims_dir(vault: Optional[Path] = None) -> Path:
    """The v7 Claim node tree, single-sourced from the structure resolver
    (hard rule #3: no hardcoded paths)."""
    return (vault or vault_root()) / _structure.atomic_claim_dir()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _content_hash(front: Dict[str, Any]) -> str:
    """sha256 over the frontmatter sans content_hash — same convention as the
    P4 atomize/learnings_to_claims writers, so a re-hash here matches a re-hash
    there."""
    payload = {k: v for k, v in front.items() if k != "content_hash"}
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _emit(front: Dict[str, Any], body: str) -> str:
    fm = yaml.safe_dump(front, sort_keys=True, allow_unicode=True,
                        default_flow_style=False)
    return f"---\n{fm}---\n\n{body.rstrip()}\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


_SLUG_RX = re.compile(r"[^0-9a-zA-Z가-힣]+")


def _slugify(s: str) -> str:
    out = _SLUG_RX.sub("-", str(s)).strip("-").lower()
    return out[:80] or "claim"


# ── read / enumerate ──────────────────────────────────────────────────────────


def is_claim(fm: Dict[str, Any]) -> bool:
    sv = fm.get("schema_version")
    return bool(isinstance(sv, int) and sv >= 7 and fm.get("kind") == "claim")


def iter_claim_files(vault: Optional[Path] = None) -> Iterator[Path]:
    """Every v7 Claim node file under the atomic claims tree (flat; INDEX
    excluded). Tolerant — unparsable files are skipped by the readers."""
    base = claims_dir(vault)
    if not base.exists():
        return
    for p in sorted(base.rglob("*.md")):
        if p.name == "INDEX.md":
            continue
        yield p


def read_claim(path: Path) -> Optional[Tuple[Dict[str, Any], str]]:
    """(frontmatter, body) for a v7 claim file, or None when it is not a claim
    or fails to parse."""
    try:
        fm, body = _parse.split_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:                       # pragma: no cover
        return None
    if not isinstance(fm, dict) or not is_claim(fm):
        return None
    return fm, body


def find_claim_by_entry_id(entry_id: str,
                           vault: Optional[Path] = None
                           ) -> Optional[Tuple[Path, Dict[str, Any], str]]:
    """Locate a claim by its stable entry_id (the link target, path-independent).
    Returns (path, fm, body) or None."""
    for p in iter_claim_files(vault):
        got = read_claim(p)
        if got is None:
            continue
        fm, body = got
        if str(fm.get("entry_id")) == str(entry_id):
            return p, fm, body
    return None


def surfacing_of(fm: Dict[str, Any]) -> str:
    s = fm.get("surfacing")
    return s if s in _LADDER else TIER_QUERY


# ── field transition (the core of promote/dream) ──────────────────────────────


def set_surfacing(path: Path, fm: Dict[str, Any], body: str, *,
                  new_tier: str, generated_by: Optional[str] = None
                  ) -> Dict[str, Any]:
    """Transition a claim's `surfacing` tier IN PLACE (RFC 0005 §7.1 — a field
    transition, not a directory move).

    - `entry_id` is PRESERVED verbatim (it is the link/ledger/R2 target, §5).
    - `content_hash` is RE-DERIVED so the projection stays consistent.
    - `generated_by` is updated when given (promote→`promote`); the original
      provenance is kept under `generated_by_history` so the lineage is not lost.
    - the file does NOT move; the same `path` is rewritten atomically.

    Returns the new frontmatter dict.
    """
    if new_tier not in _LADDER:
        raise ValueError(f"unknown surfacing tier: {new_tier!r}")
    new_fm = dict(fm)
    old_tier = surfacing_of(fm)
    new_fm["surfacing"] = new_tier
    if generated_by:
        prev = new_fm.get("generated_by")
        if prev and prev != generated_by:
            hist = list(new_fm.get("generated_by_history") or [])
            if prev not in hist:
                hist.append(prev)
            new_fm["generated_by_history"] = hist
        new_fm["generated_by"] = generated_by
    new_fm["surfaced_at"] = _now_iso()
    new_fm.pop("content_hash", None)
    new_fm["content_hash"] = _content_hash(new_fm)
    _atomic_write(path, _emit(new_fm, body))
    new_fm["_prev_surfacing"] = old_tier      # for the caller's report only
    return new_fm


# ── synthesis (dream ② — agent-supplied text, engine-written node) ────────────


def write_synthesized_claim(*, statement: str,
                            source_claim_ids: List[str],
                            source_entry_ids_for_id: Optional[List[str]] = None,
                            is_about: Optional[List[str]] = None,
                            rel: str = "refines",
                            why: str = "",
                            domain: str = "operational",
                            sensitivity: str = "public",
                            surfacing: str = TIER_ALWAYS,
                            project: Optional[str] = None,
                            context: Optional[str] = None,
                            body: Optional[str] = None,
                            vault: Optional[Path] = None,
                            ) -> Dict[str, Any]:
    """Mint a NEW v7 Claim that generalizes `source_claim_ids` (RFC 0005 §7.1:
    dream "synthesize[s] new Claims … linked by refines/supports, derived_from
    the source claims").

    - `generated_by: dream`, `surfacing: always` (the synthesized generalization
      is what earns the T0 budget) by default.
    - `links: [{to, rel, why}]` back to each source claim (rel ∈ supports|refines).
    - `derived_from` carries the source claims' OWN sources (PROV chain) when the
      caller passes them; else the synthesized claim is derived_from the source
      claim ids themselves so the provenance is never empty.
    - entry_id is content-addressed via the resolver (§5) so a re-run with the
      same statement + sources is idempotent (same id → same file).

    Returns {path, entry_id, slug, links}.
    """
    if rel not in ("supports", "refutes", "refines"):
        raise ValueError(f"unknown link rel: {rel!r}")
    statement = " ".join(str(statement).split())
    if not statement:
        raise ValueError("synthesized claim requires a statement")
    if not source_claim_ids:
        raise ValueError("synthesized claim requires source_claim_ids")

    vault = vault if vault is not None else vault_root()
    # derived_from: the source claims' upstream sources when supplied; else the
    # source claim ids (so PROV-O wasDerivedFrom is never empty).
    derived_from = list(dict.fromkeys(source_entry_ids_for_id or source_claim_ids))

    # content-addressed id (§5): normalize(statement) | derived_from.
    eid = _structure.entry_id(
        "claim", statement=statement,
        derived_from="|".join(sorted(source_claim_ids)),
    )

    links = [{"to": sid, "rel": rel, "why": why or "generalized by dream"}
             for sid in dict.fromkeys(source_claim_ids)]

    front: Dict[str, Any] = {
        "entry_id": eid,
        "schema_version": 7,
        "kind": "claim",
        "created_at": _now_iso(),
        "statement": statement,
        "is_about": list(dict.fromkeys(is_about or [])),
        "derived_from": derived_from,
        "attributed_to": "atelier-dream",
        "generated_by": "dream",
        "surfacing": surfacing,
        "domain": domain,
        "sensitivity": sensitivity,
        "links": links,
    }
    if project:
        front["project"] = project
    if context:
        front["context"] = context
    front["content_hash"] = _content_hash(front)

    md_body = body or (f"## Synthesis\n\n{statement}\n\n"
                       f"Generalized from {len(source_claim_ids)} source "
                       f"claim(s) by a dream pass (RFC 0005 §7.1).\n")
    out = claims_dir(vault) / f"{_slugify(statement)}-{eid[:8]}.md"
    _atomic_write(out, _emit(front, md_body))
    return {"path": str(out), "entry_id": eid, "slug": out.stem, "links": links}
