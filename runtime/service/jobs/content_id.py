"""Content-based entry_id derivation for PENDING resolution (RFC 0005 P1.3).

The path-based `atelier:{rel}` form is dropped: a doc's id must come from the
doc itself, not from where it happens to sit on disk. When a writer leaves
`entry_id: PENDING`, the hygiene pipeline (`fix_pending`, `prepare_commit`)
resolves it HERE, deriving a stable id from the doc's own creation timestamp
plus a stable discriminator read from its frontmatter (title, else filename).

This resolves a *placeholder*; it never recomputes an already-assigned id.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ...structure import resolver as _structure


def _created_at(fm: Dict[str, Any]) -> str:
    """Extract a stable creation timestamp from frontmatter.

    `created_at` is the v4 many-valued form (list of {value, ...}); `created`
    is the scalar form used by workshop docs. Empty string when neither is set
    — the id stays deterministic for a given (timestamp, discriminator) pair.
    """
    ca = fm.get("created_at")
    if isinstance(ca, list) and ca:
        first = ca[0]
        if isinstance(first, dict) and first.get("value"):
            return str(first["value"])
    if isinstance(ca, str) and ca:
        return ca
    created = fm.get("created") or fm.get("captured_at")
    return str(created) if created else ""


def _discriminator(fm: Dict[str, Any], path: Path) -> str:
    """A stable discriminator for the doc: its title, else its filename stem."""
    title = fm.get("title")
    if isinstance(title, str) and title.strip() and title.strip().lower() != "null":
        return title.strip()
    return path.stem


def entry_id_for(fm: Dict[str, Any], path: Path) -> str:
    """Content-based entry_id for a doc whose id is being resolved from PENDING."""
    return _structure.entry_id(
        "source",
        created_at=_created_at(fm),
        discriminator=_discriminator(fm, path),
    )
