"""Walk a space, detect changes by mtime + content_hash, yield work items."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from ..util import fs


@dataclass
class CrawlItem:
    slug: str
    path: Path
    mtime: float
    content_hash: str
    db_id: Optional[int]
    needs_reindex: bool


def crawl_space(
    conn: sqlite3.Connection,
    space: str,
    root: Path,
    full: bool = False,
) -> Iterator[CrawlItem]:
    existing = {
        r["slug"]: r
        for r in conn.execute(
            "SELECT id, slug, mtime, content_hash FROM pages WHERE space=?",
            (space,),
        )
    }
    seen: set[str] = set()

    for path in fs.walk_indexable(root):
        slug = fs.slug_for(root, path)
        seen.add(slug)
        st = path.stat()
        existing_row = existing.get(slug)
        if not full and existing_row and existing_row["mtime"] >= st.st_mtime:
            continue
        ch = fs.file_hash(path)
        if not full and existing_row and existing_row["content_hash"] == ch:
            # mtime moved but content didn't; still update mtime
            conn.execute(
                "UPDATE pages SET mtime=? WHERE id=?",
                (st.st_mtime, existing_row["id"]),
            )
            continue
        yield CrawlItem(
            slug=slug,
            path=path,
            mtime=st.st_mtime,
            content_hash=ch,
            db_id=existing_row["id"] if existing_row else None,
            needs_reindex=True,
        )

    # Remove pages no longer on disk (within this space only)
    stale = set(existing) - seen
    for slug in stale:
        conn.execute("DELETE FROM pages WHERE space=? AND slug=?", (space, slug))


def list_slugs(conn: sqlite3.Connection, space: str) -> list[str]:
    return [r["slug"] for r in conn.execute(
        "SELECT slug FROM pages WHERE space=? ORDER BY slug", (space,)
    )]
