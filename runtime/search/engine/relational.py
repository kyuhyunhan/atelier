"""Relational mode — graph traversal retrieval (RFC 0002).

Contract only in P0. The implementation (`LinkRelational`, wrapping today's
`runtime/search/graph.py` BFS over the `links` table — wikilinks + concept edges)
lands in **P4**, wired into the resolver as floor-ratio-gated post-fusion boosts.

The graph already exists (it powers `atelier_links`); P4 is purely about *wiring
it into recall* as a third retrieval signal, which is why this is a contract now
and a thin adapter later — no new graph code, just a new consumer.
"""
from __future__ import annotations

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
