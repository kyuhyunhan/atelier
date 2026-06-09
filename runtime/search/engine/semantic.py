"""Semantic mode — vector kNN retrieval (RFC 0002).

Contract only in P0. The implementation (`VecSemantic` over a `sqlite-vec` `vec0`
virtual table) lands in **P2**, together with the embedding gateway
(`runtime/ai/gateway.py`).

The seam is deliberate: this searcher consumes a *pre-computed* embedding. "How
do I turn text into a vector" (the gateway) is separate from "where do I store
and kNN-search vectors" (this searcher). That keeps provider choice (local Ollama
vs hosted) out of the storage backend, and lets `PgVectorSemantic` drop in later
without touching the gateway.
"""
from __future__ import annotations

from typing import Iterable, List, Protocol, Sequence, runtime_checkable

from .types import Candidate, Scope, VectorRow


@runtime_checkable
class SemanticSearcher(Protocol):
    """Vector similarity retrieval over embedded chunks.

    `search` takes a query embedding (the gateway's job to produce) and returns
    the `k` nearest chunks as page-level `Candidate`s. `index_vectors` is the
    write side called at reindex; backends re-embed only rows whose
    `VectorRow.signature` is stale (cost control, RFC 0002 §5/§9)."""

    def search(self, embedding: Sequence[float], *, scope: Scope = Scope(),
               k: int = 10) -> List[Candidate]:
        ...

    def index_vectors(self, rows: Iterable[VectorRow]) -> None:
        ...
