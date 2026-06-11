"""Semantic mode — vector kNN retrieval (RFC 0002).

Contract: `SemanticSearcher` (read-only). P2 implementation: `VecSemantic`,
kNN over the vectors.db sidecar (`vecstore.VecStore`) joined back to pages in
the main DB via chunk_id.

The contract is deliberately READ-only. P0 sketched an `index_vectors` write
method here; implementing P2 showed the write side (content-hash cache, stale
detection, batch sync) is backend-specific — a pgvector backend would sync by
entirely different means — while the resolver only ever *reads*. So the write
side lives on the backend (`VecStore.sync`), not in the cross-backend contract.

The seam from RFC §5 stands: this searcher consumes a *pre-computed* query
embedding. Producing it (the gateway) is `runtime/ai`'s job, so provider choice
never leaks into storage.
"""
from __future__ import annotations

import sqlite3
from typing import List, Protocol, Sequence, runtime_checkable

from .types import Candidate, Scope
from .vecstore import VecStore


@runtime_checkable
class SemanticSearcher(Protocol):
    """Vector-similarity retrieval over embedded chunks.

    Takes a query embedding, returns at most `k` page-level `Candidate`s,
    nearest first, deduplicated to one per page. `score` is the vector
    distance (smaller = nearer), mode-native per the `Candidate` contract."""

    def search(self, embedding: Sequence[float], *, scope: Scope = Scope(),
               k: int = 10) -> List[Candidate]:
        ...


class VecSemantic:
    """kNN over vectors.db, joined to pages in the main DB.

    Holds the main-DB connection (for the chunk→page join) and an open
    `VecStore`. Construction is the caller's job precisely because the store
    may be unavailable (`VecStore.open() is None`) — in that case the engine
    bundle's `semantic` slot stays None and nothing here runs."""

    def __init__(self, conn: sqlite3.Connection, store: VecStore) -> None:
        self._conn = conn
        self._store = store

    def search(self, embedding: Sequence[float], *, scope: Scope = Scope(),
               k: int = 10) -> List[Candidate]:
        if not embedding:
            return []
        # Over-fetch: several nearest chunks may share a page, and scope may
        # discard hits — same over-fetch-then-collapse pattern as FtsLexical.
        pairs = self._store.knn(list(embedding), k=max(k, 1) * 8)
        if not pairs:
            return []
        by_chunk = {cid: dist for cid, dist in pairs}
        placeholders = ",".join("?" * len(by_chunk))
        sql = [
            "SELECT chunks.id AS chunk_id, p.id AS page_id, p.slug,",
            "       p.page_type, p.space,",
            "       substr(chunks.text, 1, 160) AS snip",
            "FROM   chunks JOIN pages p ON p.id = chunks.page_id",
            f"WHERE  chunks.id IN ({placeholders})",
        ]
        params: list = list(by_chunk)
        if scope.space:
            sql.append("AND p.space = ?")
            params.append(scope.space)
        if scope.page_types:
            tp = ",".join("?" * len(scope.page_types))
            sql.append(f"AND p.page_type IN ({tp})")
            params.extend(scope.page_types)

        rows = sorted(self._conn.execute("\n".join(sql), params),
                      key=lambda r: by_chunk[r["chunk_id"]])
        out: List[Candidate] = []
        seen: set[str] = set()
        for r in rows:
            if r["slug"] in seen:
                continue
            seen.add(r["slug"])
            out.append(Candidate(
                page_id=r["page_id"], slug=r["slug"], page_type=r["page_type"],
                score=by_chunk[r["chunk_id"]], snippet=r["snip"] or "",
            ))
            if len(out) >= k:
                break
        return out
