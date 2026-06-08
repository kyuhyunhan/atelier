"""Accepted-learning store layout (RFC 0001).

Classification lives in frontmatter facets, not the path. Accepted learnings are
a FLAT store sharded only by immutable creation month:

    learnings/notes/<YYYY-MM>/<slug>.md

This module is the single place that knows that layout. Every enumerator reads
through `iter_accepted_files` so the by-topic → notes/ move touches one helper,
not a dozen call sites.

During the migration window the legacy `accepted/by-topic` tree is enumerated
too (its files are unread duplicates once moved, but seeding/tests and a
half-run migration must not lose them). The by-project mirror is never read —
project is a facet, not a location. P7 deletes the legacy trees and drops the
legacy root here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, List, Optional


def notes_root(vault: Path) -> Path:
    return vault / "learnings" / "notes"


# Legacy canonical root, kept readable during the migration window (removed P7).
_LEGACY_ACCEPTED = ("learnings", "accepted", "by-topic")


def accepted_roots(vault: Path) -> List[Path]:
    """Filesystem roots that may hold accepted-learning markdown, new first."""
    return [notes_root(vault), vault.joinpath(*_LEGACY_ACCEPTED)]


def iter_accepted_files(vault: Path) -> Iterator[Path]:
    """Every accepted-learning markdown file, across the flat store and the
    legacy by-topic tree. The by-project mirror is intentionally excluded."""
    seen: set = set()
    for root in accepted_roots(vault):
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.md")):
            if "by-project" in p.parts:
                continue
            key = p.resolve()
            if key in seen:
                continue
            seen.add(key)
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
