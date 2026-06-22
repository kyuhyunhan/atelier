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


def find_claim_by_slug_or_id(needle: str,
                             vault: Optional[Path] = None
                             ) -> Optional[Tuple[Path, Dict[str, Any], str]]:
    """Locate a claim by entry_id, full file stem, or the bare filename.

    The accept/archive/retract callers pass either the claim's entry_id (the
    stable handle the review listing surfaces) or the file stem. entry_id is
    tried first because it is unambiguous; the stem match is a convenience for
    hand-driven CLI use."""
    want = str(needle).removesuffix(".md")
    by_id = find_claim_by_entry_id(want, vault)
    if by_id is not None:
        return by_id
    for p in iter_claim_files(vault):
        if p.stem == want:
            got = read_claim(p)
            if got is not None:
                return p, got[0], got[1]
    return None


# ── born-as-claim: capture / absorb write a v7 Claim directly ─────────────────


def _resolve_entity_id(pref_label: str, *, sensitivity: str,
                       in_scheme: str, vault: Path) -> str:
    """Resolve-or-create a v7 Entity for `pref_label`, returning its entry_id.

    entry_id is content-addressed (`type | norm(pref_label)`, RFC 0005 §5), so a
    resolve and a create converge on the same id — the dedup key. When no node
    file exists yet we mint a thin Concept entity so `is_about` always points at a
    real node (the projection's referential integrity, doctor v7). Idempotent: a
    second capture touching the same subject reuses the file."""
    pref_label = " ".join(str(pref_label).split())
    eid = _structure.entry_id("entity", type="Concept", pref_label=pref_label)
    found = find_entity_by_entry_id(eid, vault)
    if found is not None:
        return eid
    front: Dict[str, Any] = {
        "entry_id": eid,
        "schema_version": 7,
        "kind": "entity",
        "type": "Concept",
        "created_at": _now_iso(),
        "pref_label": pref_label,
        "alt_label": [],
        "in_scheme": [in_scheme],
        "sensitivity": sensitivity,
        "links": [],
    }
    front["content_hash"] = _content_hash(front)
    out = (vault / _structure.atomic_entity_dir()
           / f"{_slugify(pref_label)}-{eid[:8]}.md")
    _atomic_write(out, _emit(front, f"# {pref_label}\n"))
    return eid


def find_entity_by_entry_id(entry_id: str, vault: Path) -> Optional[Path]:
    base = vault / _structure.atomic_entity_dir()
    if not base.exists():
        return None
    for p in sorted(base.rglob("*.md")):
        if p.name == "INDEX.md":
            continue
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:                       # pragma: no cover
            continue
        if isinstance(fm, dict) and str(fm.get("entry_id")) == str(entry_id):
            return p
    return None


# NOTE (RFC 0005 P10): the per-learning `mint_session_source` writer is GONE.
# Operational learnings no longer mint a thin session-metadata Source stub each;
# they all derive_from the single shared operational-capture Source below, and
# the per-capture session metadata lives ON the claim (§4.3 extension fields).


# ── the single shared operational-capture Source (RFC 0005 P10) ───────────────
#
# RFC 0005 P10 simplifies operational-learning provenance: instead of minting a
# per-learning thin session-metadata Source (one stub per claim, cluttering the
# inbox and redundant with the §4.3 claim extension fields), every operational
# claim derives_from ONE shared canonical L1 Source. The session metadata that
# used to live on that per-learning stub (agent_kind/hook/session_id/working_dir/
# captured_at) now lives ON the claim as §4.3 extension fields.
#
# The shared source has a FIXED, resolver-derived entry_id that does NOT depend
# on any session discriminator or the wall-clock — it is the same node for every
# capture, forever. `ensure_operational_source()` creates it once (idempotent)
# and returns its id.

# The fixed basis for the shared source's id. created_at is held empty so the id
# never depends on wall-clock; the discriminator is the canonical literal — the
# id is uuid5(NS, "atelier:source:|atelier:operational-capture"), stable forever.
_OPERATIONAL_SOURCE_DISCRIMINATOR = "atelier:operational-capture"


def operational_source_id() -> str:
    """The fixed entry_id of the single shared operational-capture Source
    (RFC 0005 P10). Resolver-derived from a stable basis (no created_at, no
    session discriminator), so it is the SAME id for every capture, forever."""
    return _structure.entry_id(
        "source", created_at="",
        discriminator=_OPERATIONAL_SOURCE_DISCRIMINATOR,
    )


def ensure_operational_source(vault: Optional[Path] = None) -> Dict[str, Any]:
    """Create-once the single shared operational-capture Source and return its
    id (RFC 0005 P10). Idempotent: if the node already exists by its fixed
    entry_id, this is a no-op read and the same id is returned.

    The Source is a canonical L1 node (kind:source, domain:inbox,
    sensitivity:public) living in the content tree under the inbox intake
    (session_source_dir(), = raw/inbox) — RFC 0005 §3: a Source IS an ingested
    artifact. Every operational Claim born by capture/absorb derives_from THIS
    one node; the per-capture session metadata lives on the Claim (§4.3), not
    on a per-learning source stub.

    Returns {path, entry_id}.
    """
    vault = vault if vault is not None else vault_root()
    eid = operational_source_id()
    out = vault / _structure.session_source_dir() / "operational-capture.md"
    # The shared source lives at a deterministic path, so an O(1) existence check
    # avoids an O(tree) rglob on every capture/absorb/principle write.
    if out.exists():
        return {"path": str(out), "entry_id": eid}
    now = _now_iso()
    front: Dict[str, Any] = {
        "entry_id": eid,
        "schema_version": 7,
        "kind": "source",
        "created_at": now,
        "domain": "inbox",
        "sensitivity": "public",
        "attributed_to": "atelier",
        "title": "Operational capture",
    }
    front["content_hash"] = _content_hash(front)
    body = (
        "Shared L1 Source for operational learnings (RFC 0005 P10).\n\n"
        "Every operational Claim (a capture or an absorbed Claude memory) "
        "derives_from this single canonical Source. The per-capture session "
        "metadata (agent_kind / hook / session_id / working_dir / captured_at) "
        "lives ON each Claim as §4.3 extension fields — not on a per-learning "
        "source stub.\n"
    )
    _atomic_write(out, _emit(front, body))
    return {"path": str(out), "entry_id": eid}


def find_source_by_entry_id(entry_id: str, vault: Path) -> Optional[Path]:
    """Locate a v7 Source node file by its stable entry_id, scanning the content
    tree (a Source is an L1 node in raw/, §3). Returns the path or None."""
    base = vault / _structure.source_scan_root()
    if not base.exists():
        return None
    for p in sorted(base.rglob("*.md")):
        if p.name == "INDEX.md":
            continue
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:                       # pragma: no cover
            continue
        if (isinstance(fm, dict) and fm.get("kind") == "source"
                and str(fm.get("entry_id")) == str(entry_id)):
            return p
    return None


def write_operational_claim(*, statement: str,
                            source_entry_id: str,
                            body: str,
                            generated_by: str,
                            attributed_to: str = "claude-code",
                            agent_kind: str = "claude-code",
                            hook: str = "manual",
                            observation_kind: str = "feedback",
                            why_status: str = "present",
                            project: Optional[str] = None,
                            is_about: Optional[List[str]] = None,
                            sensitivity: str = "public",
                            surfacing: str = TIER_QUERY,
                            ac_status: str = "pending",
                            captured_at: Optional[str] = None,
                            extra: Optional[Dict[str, Any]] = None,
                            vault: Optional[Path] = None,
                            ) -> Dict[str, Any]:
    """Write a v7 operational Claim born at `surfacing:query, ac_status:pending`
    (RFC 0005 §7.1 — an operational learning is BORN AS A CLAIM, never a
    candidate file).

    - `domain: operational`, `derived_from: [source_entry_id]` (the single
      shared operational-capture Source, RFC 0005 P10 — NOT a per-learning
      session stub). The per-capture session metadata (agent_kind / hook /
      session_id / working_dir / captured_at) is carried ON this claim as §4.3
      extension fields.
    - entry_id is content-addressed (`norm(statement) | derived_from`, §5) so a
      re-capture of the same lesson from the same source is idempotent.
    - `is_about` points at resolved Entity ids; `project` is kept as a flat field
      AND mirrored to `project_hint` so the existing acceptance criteria
      (`has_project_tag`) and recall project filter keep working unchanged.
    - the body preserves the `## Observation / ## Why this matters /
      ## Applicable rule` sections the acceptance criteria heuristics read.

    Returns {path, entry_id, slug}.
    """
    vault = vault if vault is not None else vault_root()
    statement = " ".join(str(statement).split())
    if not statement:
        raise ValueError("operational claim requires a statement")
    now = captured_at or _now_iso()
    eid = _structure.entry_id("claim", statement=statement,
                              derived_from=source_entry_id)
    front: Dict[str, Any] = {
        "entry_id": eid,
        "schema_version": 7,
        "kind": "claim",
        "created_at": now,
        "statement": statement,
        "domain": "operational",
        "sensitivity": sensitivity,
        "surfacing": surfacing,
        "ac_status": ac_status,
        "derived_from": [source_entry_id],
        "is_about": list(dict.fromkeys(is_about or [])),
        "attributed_to": attributed_to or "unknown",
        "agent_kind": agent_kind or "unknown",
        "generated_by": generated_by,
        "hook": hook or "manual",
        "observation_kind": observation_kind,
        "why_status": why_status,
        "links": [],
    }
    if project:
        front["project"] = project
        front["project_hint"] = project       # criteria/recall read this name
    if captured_at:
        front["captured_at"] = captured_at
    if extra:
        front.update(extra)
    front["content_hash"] = _content_hash(front)
    out = claims_dir(vault) / f"{_slugify(statement)}-{eid[:8]}.md"
    # Collision-avoid: a distinct lesson that slugifies the same keeps its own id.
    n = 1
    while out.exists() and str(_safe_eid(out)) != eid:
        out = claims_dir(vault) / f"{_slugify(statement)}-{eid[:8]}-{n}.md"
        n += 1
    _atomic_write(out, _emit(front, body))
    return {"path": str(out), "entry_id": eid, "slug": out.stem}


def _safe_eid(path: Path) -> Optional[str]:
    got = read_claim(path)
    return got[0].get("entry_id") if got else None


def set_ac_status(path: Path, fm: Dict[str, Any], body: str, *,
                  new_status: str,
                  archive_reason: Optional[str] = None,
                  links: Optional[List[str]] = None,
                  ac_results: Optional[Dict[str, Any]] = None,
                  ) -> Dict[str, Any]:
    """Transition a claim's acceptance state IN PLACE (RFC 0005 §7.1 — accept /
    archive / retract are FIELD transitions on the claim, not directory moves).

    - `entry_id` PRESERVED; `content_hash` re-derived.
    - `passed` (accept) stamps `accepted_at`; `failed`/`retracted` stamp
      `archived_at` + `archive_reason`.
    - the file does NOT move; the same `path` is rewritten atomically.

    Returns the new frontmatter dict.
    """
    new_fm = dict(fm)
    new_fm["ac_status"] = new_status
    if new_status == "passed":
        new_fm["accepted_at"] = _now_iso()
    if new_status in ("failed", "retracted"):
        new_fm["archived_at"] = _now_iso()
        if archive_reason:
            new_fm["archive_reason"] = archive_reason
    if links:
        new_fm["links"] = list(dict.fromkeys(
            list(new_fm.get("links") or []) + list(links)))
    if ac_results is not None:
        new_fm["ac_results"] = ac_results
    new_fm.pop("content_hash", None)
    new_fm["content_hash"] = _content_hash(new_fm)
    _atomic_write(path, _emit(new_fm, body))
    return new_fm


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
