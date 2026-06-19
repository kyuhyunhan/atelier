"""Accepted-learning store layout (RFC 0001).

Classification lives in frontmatter facets, not the path. Accepted learnings are
a FLAT store sharded only by immutable creation month:

    provenance/learning/notes/<YYYY-MM>/<slug>.md   (RFC 0003 P6; legacy: learnings/)

This module is the single place that knows that layout. Every enumerator reads
through `iter_accepted_files`, so the layout is defined once, not at a dozen call
sites. (The legacy accepted/by-topic|by-project trees were migrated and removed
in RFC 0001; only the flat notes/ store remains.)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, List, Optional

from ...structure import resolver as _structure


def learning_root(vault: Path) -> Path:
    """Base of the learnings subtree — the ONE place that knows where it lives.

    RFC 0003 P6 relocates the tree from top-level `learnings/` to
    `provenance/learning/` (finishing the §4 directory vision P1/GP1 left undone).
    During the transition we resolve to whichever tree is on disk, so the vault
    `git mv` (V1) flips every reader and writer atomically with no dangling — the
    same dual-path discipline that kept GP1 safe.

    Resolves to `provenance/learning/` when it exists on disk (the canonical home
    after the P6 move), else falls back to the legacy top-level `learnings/`. The
    fallback is kept as permanent backward-compat: a vault that predates the move,
    or a fixture that seeds `learnings/`, still resolves correctly. The gorae vault
    is migrated; this resolver is what made the `git mv` a non-event to every
    reader and writer."""
    # Canonical home (content_root/learning) and its pre-P6 top-level alias
    # (learnings/) are both single-sourced from structure.yaml (RFC 0005 P1):
    # the alias is the reverse of the prefix_aliases learnings/ mapping.
    canonical_rel = f"{_structure.content_root()}/learning"
    legacy_rel = _structure.prefix_aliases()[f"{canonical_rel}/"].rstrip("/")
    new = vault / canonical_rel
    if new.exists():
        return new
    return vault / legacy_rel


def notes_root(vault: Path) -> Path:
    return learning_root(vault) / "notes"


def accepted_roots(vault: Path) -> List[Path]:
    """Filesystem roots that hold accepted-learning markdown."""
    return [notes_root(vault)]


def iter_accepted_files(vault: Path) -> Iterator[Path]:
    """Every accepted-learning markdown file.

    RFC 0005 §7.1 — an accepted operational learning is now a v7 Claim at
    `ac_status: passed` (a FIELD, not a notes/ file). This iterator is the single
    chokepoint every accepted-pool reader (recall / search / bootstrap /
    project / principles / surfacing / eval / cluster) goes through, so yielding
    accepted operational claims HERE migrates them all at once, with no edit at
    the dozen call sites.

    For back-compat it still yields any legacy `notes/<YYYY-MM>/*.md` on disk —
    a vault that predates the claim migration, or a fixture that seeds notes/,
    keeps resolving."""
    seen: set = set()
    root = notes_root(vault)
    if root.exists():
        for p in sorted(root.rglob("*.md")):
            seen.add(p.resolve())
            yield p
    yield from _iter_accepted_claims(vault, seen)


def _iter_accepted_claims(vault: Path, seen: set) -> Iterator[Path]:
    """Accepted operational claims (domain:operational, ac_status:passed).

    Read lazily and tolerantly: a claim that fails to parse is skipped, never
    crashing a reader. Imported locally to avoid a module import cycle
    (store ← claims_io ← structure ← … ); the readers only ever call the
    public iterator."""
    from ...index import parse as _parse
    from ...structure import resolver as _structure
    base = vault / _structure.atomic_claim_dir()
    if not base.exists():
        return
    for p in sorted(base.rglob("*.md")):
        if p.name == "INDEX.md" or p.resolve() in seen:
            continue
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:                       # pragma: no cover
            continue
        if not isinstance(fm, dict):
            continue
        if (fm.get("kind") == "claim"
                and str(fm.get("domain") or "") == "operational"
                and str(fm.get("ac_status") or "") == "passed"):
            yield p


_MONTH_RX = re.compile(r"(\d{4})-(\d{2})")


def month_shard(captured_at: Optional[str], *, fallback: str = "undated") -> str:
    """The <YYYY-MM> shard for a note, from its immutable `captured_at`.

    Sharding on creation month (not accepted_at, which can change on re-accept)
    keeps a record's location stable. Date is the ONLY thing in the path — it is
    not classification, so it never needs reorganizing.
    """
    if isinstance(captured_at, str):
        m = _MONTH_RX.match(captured_at.strip())
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    return fallback


def flat_dest(vault: Path, captured_at: Optional[str], filename: str) -> Path:
    """Destination path in the flat store for a note with the given filename."""
    return notes_root(vault) / month_shard(captured_at) / filename
