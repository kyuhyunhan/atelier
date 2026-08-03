"""Canonical vault structure resolver (RFC 0005 P1).

Single source for vault paths and entry_ids. Every consumer derives from
schema/data/structure.yaml via this package; no inline path/uuid literals.
"""
from __future__ import annotations

from runtime.structure.resolver import (
    content_prefixes,
    content_root,
    entry_id,
    expand_content_root,
    graph_prefixes,
    graph_root,
    home,
    inbox_dir,
    inbox_subpath,
    intake_dir,
    intake_subpath,
    known_prefixes,
    legacy_content_root,
    legacy_graph_root,
    prefix_aliases,
    shorthand_bases,
)

__all__ = [
    "content_prefixes",
    "content_root",
    "entry_id",
    "expand_content_root",
    "graph_prefixes",
    "graph_root",
    "home",
    "inbox_dir",
    "inbox_subpath",
    "intake_dir",
    "intake_subpath",
    "known_prefixes",
    "legacy_content_root",
    "legacy_graph_root",
    "prefix_aliases",
    "shorthand_bases",
]
