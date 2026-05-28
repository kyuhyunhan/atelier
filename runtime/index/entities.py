"""Maintain the entities table from wiki/entities/*.md pages."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict


def upsert_entity_from_page(
    conn: sqlite3.Connection,
    slug: str,
    frontmatter: Dict[str, Any],
) -> None:
    """Called per entity page during reindex. Idempotent."""
    aliases = frontmatter.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = []
    first_mention = frontmatter.get("first_mention")
    conn.execute(
        "INSERT INTO entities(canonical_slug, aliases, first_mention, confidence) "
        "VALUES (?, ?, ?, 1.0) "
        "ON CONFLICT(canonical_slug) DO UPDATE SET "
        "  aliases       = excluded.aliases, "
        "  first_mention = excluded.first_mention",
        (slug, json.dumps(aliases), first_mention),
    )


def prune_orphan_entities(conn: sqlite3.Connection) -> int:
    """Drop entity rows whose page no longer exists."""
    cur = conn.execute(
        "DELETE FROM entities WHERE canonical_slug NOT IN (SELECT slug FROM pages)"
    )
    return cur.rowcount or 0
