"""Canonical vault structure resolver (RFC 0005 P1).

Single source for vault paths and entry_ids. Every consumer derives from
schema/data/structure.yaml via this package; no inline path/uuid literals.
"""
from __future__ import annotations

from runtime.structure.resolver import (
    content_root,
    legacy_content_root,
    graph_root,
    legacy_graph_root,
    expand_content_root,
    content_prefixes,
    graph_prefixes,
    intake_dir,
    intake_subpath,
    inbox_subpath,
    inbox_dir,
    home,
    prefix_aliases,
    known_prefixes,
    shorthand_bases,
    entry_id,
)

__all__ = [
    "content_root",
    "legacy_content_root",
    "graph_root",
    "legacy_graph_root",
    "expand_content_root",
    "content_prefixes",
    "graph_prefixes",
    "intake_dir",
    "intake_subpath",
    "inbox_subpath",
    "inbox_dir",
    "home",
    "prefix_aliases",
    "known_prefixes",
    "shorthand_bases",
    "entry_id",
]
