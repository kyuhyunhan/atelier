"""Relational mode — graph traversal retrieval (RFC 0002).

Contract only in P0. The implementation (`LinkRelational`, wrapping today's
`runtime/search/graph.py` BFS over the `links` table — wikilinks + concept edges)
lands in **P4**, wired into the resolver as floor-ratio-gated post-fusion boosts.

The graph already exists (it powers `atelier_links`); P4 is purely about *wiring
it into recall* as a third retrieval signal, which is why this is a contract now
and a thin adapter later — no new graph code, just a new consumer.
"""
from __future__ import annotations

import sqlite3

from typing import List, Protocol, Sequence, runtime_checkable

from .types import Candidate, Scope


@runtime_checkable
class RelationalSearcher(Protocol):
    """Graph-expansion retrieval: given seed pages (the fused top hits), return
    their graph neighbors as `Candidate`s so a relevant page one hop away from a
    strong hit can surface even if it matched no query term directly.

    `seeds` are `pages.id` values. `score` on each returned `Candidate` encodes
    proximity (e.g. inverse hop-distance)."""

    def search(self, seeds: Sequence[int], *, scope: Scope = Scope(),
               k: int = 10) -> List[Candidate]:
        ...


class LinkRelational:
    """Graph-expansion over the `links` table (RFC 0002 P4).

    Given seed page_ids (the fused top hits), BFS the resolved link graph and
    return the neighbours as `Candidate`s, scored by inverse hop-distance. Pure
    SQL over the main DB — no gateway, no sidecar — so the relational mode is
    always available once edges exist (which is what RFC 0003's stub backfill
    delivered: learnings now reach concept-sibling learnings via shared entities).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def search(self, seeds: Sequence[int], *, scope: Scope = Scope(),
               k: int = 10) -> List[Candidate]:
        from ..graph import neighbors_by_id
        from ..engine.sqlite_scope import scope_where

        seed_ids = [int(s) for s in seeds]
        if not seed_ids:
            return []
        dist = neighbors_by_id(self._conn, seed_ids, depth=2)
        if not dist:
            return []
        ids = list(dist)
        ph = ",".join("?" * len(ids))
        sql = [
            "SELECT p.id, p.slug, p.page_type,",
            "  substr((SELECT c.text FROM chunks c WHERE c.page_id=p.id "
            "          ORDER BY c.position LIMIT 1), 1, 160) AS snip",
            f"FROM pages p WHERE p.id IN ({ph})",
        ]
        params: list = list(ids)
        clauses, sp = scope_where(scope, "p")
        sql.extend(clauses)
        params.extend(sp)
        rows = {r["id"]: r for r in self._conn.execute("\n".join(sql), params)}
        out: List[Candidate] = []
        # Nearer neighbours first; id as a stable, content-free tie-break.
        for pid in sorted(dist, key=lambda x: (dist[x], x)):
            r = rows.get(pid)
            if r is None:                       # dropped by a scope filter
                continue
            out.append(Candidate(
                page_id=pid, slug=r["slug"], page_type=r["page_type"],
                score=1.0 / (1 + dist[pid]), snippet=r["snip"] or ""))
            if len(out) >= k:
                break
        return out
