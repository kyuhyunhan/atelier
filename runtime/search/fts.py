"""FTS5 keyword search over chunks."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Hit:
    slug: str
    space: str
    page_type: str
    title: Optional[str]
    snippet: str
    rank: float


def search(
    conn: sqlite3.Connection,
    query: str,
    space: Optional[str] = None,
    limit: int = 20,
) -> List[Hit]:
    sql = """
        SELECT p.slug, p.space, p.page_type, p.title,
               snippet(chunks_fts, 0, '[', ']', '…', 16) AS snip,
               rank
        FROM   chunks_fts
        JOIN   chunks ON chunks.id = chunks_fts.rowid
        JOIN   pages p ON p.id     = chunks.page_id
        WHERE  chunks_fts MATCH ?
    """
    params: list = [query]
    if space:
        sql += " AND p.space = ?"
        params.append(space)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    out: List[Hit] = []
    for r in conn.execute(sql, params):
        out.append(Hit(
            slug=r["slug"], space=r["space"], page_type=r["page_type"],
            title=r["title"], snippet=r["snip"], rank=r["rank"],
        ))
    return out


def search_like_fallback(
    conn: sqlite3.Connection,
    query: str,
    space: Optional[str] = None,
    limit: int = 20,
) -> List[Hit]:
    """LIKE fallback when FTS tokenizer can't parse the query."""
    sql = """
        SELECT p.slug, p.space, p.page_type, p.title,
               substr(c.text, max(1, instr(c.text, ?) - 30), 120) AS snip
        FROM   chunks c
        JOIN   pages p ON p.id = c.page_id
        WHERE  c.text LIKE ?
    """
    params: list = [query, f"%{query}%"]
    if space:
        sql += " AND p.space = ?"
        params.append(space)
    sql += " LIMIT ?"
    params.append(limit)

    out: List[Hit] = []
    for r in conn.execute(sql, params):
        out.append(Hit(
            slug=r["slug"], space=r["space"], page_type=r["page_type"],
            title=r["title"], snippet=r["snip"], rank=0.0,
        ))
    return out
