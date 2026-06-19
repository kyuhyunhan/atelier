"""RFC 0005 §7.2 — the atomize nudge (un-atomized source backlog).

Atomization (L1 Source → L2 Claims/Entities) is the one ongoing trigger the RFC
keeps *human-gated*: it needs LLM judgement, costs tokens, and touches private
material, so there is no blind cron. Instead — exactly like the dream nudge
(`dream.nudge_info`) — a cadence *surfaces* the backlog so nothing silently
backs up, and the human runs `vault-ingest`.

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
    """entry_id of every v7 Source node."""
    out: Set[str] = set()
    base = vault / _structure.atomic_source_dir()
    for fm in _iter_fm(base):
        if fm.get("kind") != "source":
            continue
        eid = fm.get("entry_id")
        if isinstance(eid, str) and eid:
            out.add(eid)
    return out


def _atomized_source_ids(vault: Path) -> Set[str]:
    """Every source id that at least one Claim is `derived_from`."""
    out: Set[str] = set()
    base = vault / _structure.atomic_claim_dir()
    for fm in _iter_fm(base):
        if fm.get("kind") != "claim":
            continue
        df = fm.get("derived_from")
        if isinstance(df, str):
            df = [df]
        if isinstance(df, list):
            for sid in df:
                if isinstance(sid, str) and sid:
                    out.add(sid)
    return out


def unatomized_count(*, vault: Optional[Path] = None) -> int:
    """Number of Source nodes with no derived Claim (RFC 0005 §7.2).

    = |sources| − |sources that ≥1 claim is derived_from|. Read-only,
    deterministic; the dangling-claim case (a claim derived_from a missing
    source) cannot lower the count because we intersect with the real source
    set."""
    vault = vault if vault is not None else _vault_root()
    sources = _source_ids(vault)
    if not sources:
        return 0
    atomized = _atomized_source_ids(vault) & sources
    return len(sources - atomized)


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
        long = (
            f"🧩 **atelier atomize** — {count} un-atomized {noun} "
            f"(a Source with no derived Claim). Ask me to run `vault-ingest` "
            f"to extract claims + entities."
        )
        short = f"🧩 atelier: {count} to atomize"

    return {"due": due, "count": count, "short": short, "long": long}
