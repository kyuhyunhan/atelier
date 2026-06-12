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
    learning is.

    `provenance` / `sensitivity` (RFC 0003) are the single-valued *fields* an
    application scopes by: a coding agent recalls `learning`+`knowledge`, an essay
    agent `personal`+`knowledge`. They are a soft query scope (None = no
    restriction), NOT a hard silo — the engine never branches on what they mean.
    Facet filtering (RFC 0001, many-valued) stays a *resolver* concern layered on
    the fused set, not a mode concern, so it is deliberately NOT here.
    """

    space: Optional[str] = None
    page_types: tuple[str, ...] = ()
    provenance: Optional[str] = None
    sensitivity: Optional[str] = None


def scope_where(scope: "Scope", alias: str = "p") -> tuple[list[str], list]:
    """SQL `AND …` clauses + bound params for a `Scope`, over the given `pages`
    alias. Shared by every mode searcher so their scope filtering can never
    diverge — add a new scope dimension here once, not per mode. `provenance` /
    `sensitivity` read the generated columns of the same name (RFC 0003)."""
    clauses: list[str] = []
    params: list = []
    if scope.space:
        clauses.append(f"AND {alias}.space = ?")
        params.append(scope.space)
    if scope.page_types:
        ph = ",".join("?" * len(scope.page_types))
        clauses.append(f"AND {alias}.page_type IN ({ph})")
        params.extend(scope.page_types)
    if scope.provenance:
        clauses.append(f"AND {alias}.provenance = ?")
        params.append(scope.provenance)
    if scope.sensitivity:
        clauses.append(f"AND {alias}.sensitivity = ?")
        params.append(scope.sensitivity)
    return clauses, params


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


# NOTE: P0 sketched a `VectorRow` write-side value object here. P2 removed it:
# the semantic write side (content-hash cache, signature stale-detection, batch
# sync) proved backend-specific and lives on the backend (vecstore.VecStore),
# not in the cross-backend contract — the resolver only reads.
