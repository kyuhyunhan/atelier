"""Lexical mode — keyword/BM25 retrieval (RFC 0002).

Contract: `LexicalSearcher`. P0 implementation: `FtsLexical`, a thin adapter over
today's FTS5 path (`runtime/search/fts.py`). Nothing routes through it yet — its
only job in P0 is to prove the contract fits the *real* backend, so the resolver
(P3) can depend on `LexicalSearcher` rather than on `fts.search` directly.

When the vault outgrows SQLite, a `PgLexical` lands here implementing the same
`LexicalSearcher` Protocol; the resolver never changes.
"""
from __future__ import annotations

import sqlite3
from typing import List, Protocol, runtime_checkable

from .. import fts as _fts
from .types import Candidate, Scope, scope_where


@runtime_checkable
class LexicalSearcher(Protocol):
    """Keyword retrieval over indexed text.

    Returns at most `k` `Candidate`s, deduplicated to one per page, ordered best
    first. A query with no usable tokens returns `[]` (never raises)."""

    def search(self, query: str, *, scope: Scope = Scope(), k: int = 10) -> List[Candidate]:
        ...


class FtsLexical:
    """FTS5 BM25 over `chunks_fts`, scoped by `pages.space` / `pages.page_type`.

    Holds a connection (DI) the way `graph.py` takes one — so tests and the
    resolver control the DB lifecycle. Reuses `fts.sanitize_match` so this path
    can never diverge from the existing search/recall sanitization."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def search(self, query: str, *, scope: Scope = Scope(), k: int = 10) -> List[Candidate]:
        match = _fts.sanitize_match(query)
        if not match:
            return []
        sql = [
            "SELECT p.id AS page_id, p.slug, p.page_type,",
            "       snippet(chunks_fts, 0, '[', ']', '…', 16) AS snip, rank",
            "FROM   chunks_fts",
            "JOIN   chunks ON chunks.id = chunks_fts.rowid",
            "JOIN   pages  p ON p.id    = chunks.page_id",
            "WHERE  chunks_fts MATCH ?",
        ]
        params: list = [match]
        clauses, scope_params = scope_where(scope, "p")
        sql.extend(clauses)
        params.extend(scope_params)
        # A page has many chunks → the same slug can match several times. Over-fetch
        # and collapse to one Candidate per page (best rank first), then truncate.
        sql.append("ORDER BY rank LIMIT ?")
        params.append(max(k, 1) * 8)

        out: List[Candidate] = []
        seen: set[str] = set()
        for r in self._conn.execute("\n".join(sql), params):
            if r["slug"] in seen:
                continue
            seen.add(r["slug"])
            out.append(Candidate(
                page_id=r["page_id"], slug=r["slug"], page_type=r["page_type"],
                score=float(r["rank"]), snippet=r["snip"] or "",
            ))
            if len(out) >= k:
                break
        return out
