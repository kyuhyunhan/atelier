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
    new = vault / "provenance" / "learning"
    if new.exists():
        return new
    return vault / "learnings"


def notes_root(vault: Path) -> Path:
    return learning_root(vault) / "notes"


def accepted_roots(vault: Path) -> List[Path]:
    """Filesystem roots that hold accepted-learning markdown."""
    return [notes_root(vault)]


def iter_accepted_files(vault: Path) -> Iterator[Path]:
    """Every accepted-learning markdown file in the flat notes/ store."""
    root = notes_root(vault)
    if not root.exists():
        return
    for p in sorted(root.rglob("*.md")):
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
