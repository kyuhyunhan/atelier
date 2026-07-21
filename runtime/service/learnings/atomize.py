"""RFC 0005 §7.2 — the atomize nudge (un-atomized source backlog).

Atomization (L1 Source → L2 Claims/Entities) is the one ongoing trigger the RFC
keeps *human-gated*: it needs LLM judgement, costs tokens, and touches private
material, so there is no blind cron. Instead — exactly like the dream nudge
(`dream.nudge_info`) — a cadence *surfaces* the backlog so nothing silently
backs up, and the human runs `atelier-atomize`.

"Un-atomized" is not a directory or a flag; it is a **derived state** (RFC 0005
§3.2 / §7.2): a Source node with **no Claim `derived_from` it**. We compute it by
diffing the set of Source `entry_id`s against the set of source ids referenced by
every Claim's `derived_from`. This is deterministic and read-only.

The decision lives here as the single source of truth, shared by the session
bootstrap model-context injection (and any statusline/hook), mirroring the dream
nudge's one-source-of-truth shape: `{due, count, short, long}`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Set

from ...index import parse as _parse
from ...structure import resolver as _structure
from ...util import config as _config


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _iter_fm(directory: Path):
    """Yield parsed frontmatter for every markdown node under `directory`.

    Tolerant: a file that fails to parse is skipped (never crashes the nudge),
    same posture as dream.nudge_info wrapping its probes."""
    if not directory.exists():
        return
    for p in sorted(directory.rglob("*.md")):
        if p.name == "INDEX.md":
            continue
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:                       # pragma: no cover
            continue
        if isinstance(fm, dict):
            yield fm


def _source_ids(vault: Path) -> Set[str]:
    """entry_id of every v7 Source node.

    RFC 0005 §3/§7.2: Source is an L1 node living in the content tree (raw/…),
    not a graph digest dir. We scan source_scan_root() (= content_root)
    recursively and keep only kind:source — so artifact-backed sources under
    raw/<domain>/ AND thin session sources under raw/inbox/ both count,
    classified by the `kind` FIELD regardless of subdir."""
    out: Set[str] = set()
    base = vault / _structure.source_scan_root()
    for fm in _iter_fm(base):
        if fm.get("kind") != "source":
            continue
        eid = fm.get("entry_id")
        if isinstance(eid, str) and eid:
            out.add(eid)
    return out


def _derived_ids(fm: Dict[str, Any]) -> Set[str]:
    """The source ids a single Claim's `derived_from` points at (normalizing the
    str-or-list shape). SINGLE definition, shared by the filesystem scan and the
    DB-projection count so the two can't disagree."""
    out: Set[str] = set()
    df = fm.get("derived_from")
    if isinstance(df, str):
        df = [df]
    if isinstance(df, list):
        for sid in df:
            if isinstance(sid, str) and sid:
                out.add(sid)
    return out


def _atomized_source_ids(vault: Path) -> Set[str]:
    """Every source id that at least one Claim is `derived_from`."""
    out: Set[str] = set()
    base = vault / _structure.atomic_claim_dir()
    for fm in _iter_fm(base):
        if fm.get("kind") != "claim":
            continue
        out |= _derived_ids(fm)
    return out


def unatomized_from_nodes(source_fms: list, claim_fms: list) -> int:
    """The un-atomized backlog computed from already-loaded frontmatter dicts
    (the DB-projection path). Same set math as `unatomized_count`, factored out
    so the filesystem scan and the projection share ONE definition."""
    sources = {fm.get("entry_id") for fm in source_fms
               if fm.get("kind") == "source"
               and isinstance(fm.get("entry_id"), str) and fm.get("entry_id")}
    if not sources:
        return 0
    atomized: Set[str] = set()
    for fm in claim_fms:
        if fm.get("kind") == "claim":
            atomized |= _derived_ids(fm)
    return len(sources - (atomized & sources))


def unatomized_count(*, vault: Optional[Path] = None) -> int:
    """Number of Source nodes with no derived Claim (RFC 0005 §7.2).

    = |sources| − |sources that ≥1 claim is derived_from|. Read-only,
    deterministic; the dangling-claim case (a claim derived_from a missing
    source) cannot lower the count because we intersect with the real source
    set.

    Reads the DB projection first (one indexed query, no markdown I/O); falls
    back to the filesystem scan when the projection can't answer (cold/empty DB
    or query error)."""
    from . import projection_counts as _pc
    projected = _pc.unatomized_sources()
    if projected is not None:
        return projected
    vault = vault if vault is not None else _vault_root()
    sources = _source_ids(vault)
    if not sources:
        return 0
    atomized = _atomized_source_ids(vault) & sources
    return len(sources - atomized)


def unatomized_by_gate(*, vault: Optional[Path] = None) -> Dict[str, int]:
    """Split the un-atomized backlog by the human-gate (Policy 1): `personal`
    = a private-domain Source (human-gated — atomize only when the human directs
    it, never a blind pass) vs `atomizable` = everything else (run the skill).
    Filesystem scan; used only when the nudge is already due, so O(sources) is
    fine at session start. Returns {'atomizable': int, 'personal': int}."""
    vault = vault if vault is not None else _vault_root()
    atomized = _atomized_source_ids(vault)
    private_domains = set(_structure.atomize_private_source_domains())
    atomizable = personal = 0
    base = vault / _structure.source_scan_root()
    for fm in _iter_fm(base):
        if fm.get("kind") != "source":
            continue
        eid = fm.get("entry_id")
        if not (isinstance(eid, str) and eid) or eid in atomized:
            continue
        if fm.get("domain") in private_domains:
            personal += 1
        else:
            atomizable += 1
    return {"atomizable": atomizable, "personal": personal}


def nudge_info(*, now: Optional[str] = None,
               vault: Optional[Path] = None) -> Dict[str, Any]:
    """Single source of the atomize-nudge decision, shaped like
    dream.nudge_info(): {due, count, short, long}.

    `due` fires when the backlog reaches `learnings.atomize.nudge_after_sources`
    (default 1 — any un-atomized source nudges; the human decides when to run a
    pass). `now` is accepted for signature parity with dream.nudge_info; the
    atomize nudge is count-driven, not time-driven, so it is unused today."""
    cfg = _config.load()
    atomize_cfg = (cfg.raw.get("learnings") or {}).get("atomize") or {}
    after = int(atomize_cfg.get("nudge_after_sources", 1))

    try:
        count = unatomized_count(vault=vault)
    except Exception:                           # pragma: no cover
        count = 0

    due = count >= after

    long = ""
    short = ""
    if due:
        noun = "source" if count == 1 else "sources"
        # Split by the human-gate so the message doesn't tell the human to run
        # `atelier-atomize` on a private diary (personal is human-gated, Policy 1).
        gate = unatomized_by_gate(vault=vault)
        a, p = gate["atomizable"], gate["personal"]
        if a and p:
            long = (
                f"🧩 **atelier atomize** — {count} un-atomized {noun} "
                f"(no derived Claim): {a} to atomize → ask me to run "
                f"`atelier-atomize`; {p} personal → private, human-gated "
                f"(atomize only when you direct it)."
            )
        elif a:
            long = (
                f"🧩 **atelier atomize** — {a} {noun} to atomize "
                f"(no derived Claim). Ask me to run `atelier-atomize` to "
                f"extract claims + entities."
            )
        else:  # personal only
            pnoun = "source" if p == 1 else "sources"
            long = (
                f"🧩 **atelier atomize** — {p} personal {pnoun} awaiting "
                f"atomization (no derived Claim). Private + human-gated — "
                f"atomize only when you direct it, not a blind pass."
            )
        short = f"🧩 atelier: {count} to atomize"

    return {"due": due, "count": count, "short": short, "long": long}
