"""FTS5 keyword search over chunks."""
from __future__ import annotations

import re
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


def sanitize_match(query: str) -> str:
    """Turn an arbitrary natural-language query into a safe FTS5 MATCH expr.

    FTS5 treats `-`, `:`, `"` etc. as operators, so a raw prompt like
    'session-end auto-commit' raises `no such column: end`. We reduce the query
    to word-class tokens, quote each, and OR them — robust for client PULL calls.
    This is the single tokenizer for every lexical path: recall and learnings
    search reach it via the resolver's `FtsLexical` mode, so they cannot diverge.
    Returns '' when the query has no usable tokens (caller returns no hits)."""
    tokens = re.findall(r"\w+", query or "", flags=re.UNICODE)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:24])


def search(
    conn: sqlite3.Connection,
    query: str,
    space: Optional[str] = None,
    limit: int = 20,
) -> List[Hit]:
    match = sanitize_match(query)
    if not match:
        return []
    sql = """
        SELECT p.slug, p.space, p.page_type, p.title,
               snippet(chunks_fts, 0, '[', ']', '…', 16) AS snip,
               rank
        FROM   chunks_fts
        JOIN   chunks ON chunks.id = chunks_fts.rowid
        JOIN   pages p ON p.id     = chunks.page_id
        WHERE  chunks_fts MATCH ?
    """
    params: list = [match]
    if space:
        sql += " AND p.space = ?"
        params.append(space)
    # A page has many chunks, so the same slug can match several times. Over-fetch
    # and collapse to one hit per page (best rank first), then truncate — so the
    # caller sees pages, not chunk rows.
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit * 8)

    out: List[Hit] = []
    seen: set = set()
    for r in conn.execute(sql, params):
        if r["slug"] in seen:
            continue
        seen.add(r["slug"])
        out.append(Hit(
            slug=r["slug"], space=r["space"], page_type=r["page_type"],
            title=r["title"], snippet=r["snip"], rank=r["rank"],
        ))
        if len(out) >= limit:
            break
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
