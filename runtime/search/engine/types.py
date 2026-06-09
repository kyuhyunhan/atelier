"""Shared value objects for the retrieval engine (RFC 0002, P0).

These types are the *vocabulary* the three mode-contracts speak — every searcher
takes a `Scope` and returns `Candidate`s, regardless of backend. Keeping them in
one place (not per-mode) is deliberate: a `Candidate` from the lexical mode and a
`Candidate` from the semantic mode MUST be the same shape, or the resolver (P3)
cannot fuse them.

Nothing here knows about SQLite, sqlite-vec, or pgvector — that is the whole
point. A backend is free to populate these however it likes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Scope:
    """The filter every mode honors before ranking.

    `space` scopes to one vault subtree (None = all spaces). `page_types`
    restricts to a set of `pages.page_type` values (empty = no restriction) —
    this is how recall scopes to `learning_*` without the engine knowing what a
    learning is. Facet filtering (RFC 0001) is a *resolver* concern layered on
    top of the fused set, not a mode concern, so it is deliberately NOT here.
    """

    space: Optional[str] = None
    page_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    """One mode's vote for one page, before fusion.

    `score` is *mode-native* and NOT cross-mode comparable: BM25 rank (smaller =
    better), cosine similarity (larger = better), or graph hop-distance. The
    resolver fuses by rank *position* via RRF (P3), never by raw score — so each
    mode is free to keep its own honest scale here.
    """

    page_id: int
    slug: str
    page_type: str
    score: float
    snippet: str = ""


@dataclass(frozen=True)
class VectorRow:
    """A chunk embedding to persist (semantic write side, P2).

    `signature` is the `embedding_signature` from RFC 0002 §5
    (provider+model+dim+chunker_version) — reindex re-embeds only rows whose
    signature is stale, so a `rm db && reindex` reuses unchanged embeddings
    instead of paying to recompute them.
    """

    page_id: int
    chunk_id: int
    embedding: Sequence[float]
    signature: str
