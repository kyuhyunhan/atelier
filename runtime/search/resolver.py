"""The hybrid resolver (RFC 0002 P3) — fuse the wired retrieval modes into one order.

This is the layer the RFC's §3 diagram names: each mode (semantic, lexical,
relational) votes independently; the resolver fuses those votes with Reciprocal
Rank Fusion and hands back one ranked candidate set. It depends only on the
engine *contract* (`RetrievalEngine` + the three Protocols), never on a concrete
backend — swapping sqlite-vec for pgvector changes a searcher, not this file.

Two layers, deliberately split:

  rrf_fuse(rankings)   PURE. Per-mode ranked id lists → one fused id order. No
                       DB, no Scope, no Candidate — just the math. Kept pure so
                       the fusion contract is pinnable without a vault.
  resolve(...)         orchestration: run each wired mode, fuse, carry the
                       display fields, return Candidates. (Lands in step 2.)

Why rank fusion and not a weighted score blend: the three modes speak
incomparable scales (BM25 rank, cosine distance, hop count — see `Candidate`).
RRF never compares raw scores; it sums `1/(C + rank)` over each mode's *position*
list, so every mode votes on equal footing and a doc strong in any one mode
survives. The P2 baseline proved semantic is not a superset of lexical — fusion,
not replacement, is what preserves both modes' unique hits.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .engine import Candidate, RetrievalEngine, Scope

# Rank-smoothing constant (Cormack, Clarke & Buettcher 2009). Larger C flattens
# the 1/(C+rank) curve so rank-1 and rank-2 sit close together — this is what
# lets a doc that is #1 in one mode but #8 in another still outrank a doc that is
# merely #2 in a single mode. Pinned and tested: changing it reshuffles results.
C_RRF = 60

# Each mode is asked for this multiple of the final k before fusion, so a doc
# that is mid-ranked in one mode but top in another still has a vote to fuse.
# (Each mode then applies its OWN k*8 row over-fetch internally before per-page
# dedup — see lexical.py / semantic.py — so this is page depth, not row depth.)
_OVERFETCH = 8

# Relational expansion seeds from the top-N primary (lexical+semantic) fused
# hits — a floor-ratio proxy: expand only from confident hits, not the tail.
_RELATIONAL_SEEDS = 10


def _rrf_scores(rankings: Sequence[Sequence[int]]) -> Dict[int, float]:
    """Fused score per id: `Σ_modes 1/(C_RRF + rank)`. The math core shared by
    `rrf_fuse` (which adds the tie-break) and `resolve` (which puts the score on
    each returned Candidate)."""
    scores: Dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, page_id in enumerate(ranking):
            scores[page_id] += 1.0 / (C_RRF + rank)
    return scores


def rrf_fuse(rankings: Sequence[Sequence[int]]) -> List[int]:
    """Reciprocal Rank Fusion over per-mode ranked id lists.

    Each inner sequence is one mode's ids, best-first (position == rank). Returns
    the ids ordered by descending fused score `Σ_modes 1/(C_RRF + rank)`. An id
    absent from a mode simply contributes nothing for that mode — no penalty, no
    imputation. Ties break by first appearance across the inputs, so the order is
    deterministic regardless of dict iteration.
    """
    scores = _rrf_scores(rankings)
    first_seen: Dict[int, int] = {}
    order = 0
    for ranking in rankings:
        for page_id in ranking:
            if page_id not in first_seen:
                first_seen[page_id] = order
                order += 1
    # Sort by score desc, then first-appearance asc for a stable, content-free
    # tie-break (never by page_id value, which would leak insertion artifacts).
    return sorted(scores, key=lambda pid: (-scores[pid], first_seen[pid]))


def resolve(query: str, *, engine: RetrievalEngine, scope: Scope = Scope(),
            gateway: Optional[object] = None, k: int = 10) -> List[Candidate]:
    """Run every wired mode over `query`, fuse with RRF, return top-`k` Candidates.

    Modes:
      lexical    always runs (the bundle requires it).
      semantic   runs only when BOTH `engine.semantic` is wired AND a `gateway`
                 is supplied to embed the query — otherwise the resolver is
                 lexical-only (the P2 degrade contract: provider down = no
                 semantic slot, never an error).

    The fused `Candidate.score` carries the RRF magnitude (larger = better), NOT
    a mode-native score — callers that threshold or boost do so on this scale.

    Snippet carry: a page hit by both modes keeps the *lexical* snippet (FTS's
    `[...]`-highlighted, query-relevant) when it is non-empty, else falls back to
    the semantic substring; a page hit only by semantic keeps the semantic
    snippet. Slug/page_type are mode-agnostic (same page), so either mode's value
    is fine.
    """
    fetch = max(k, 1) * _OVERFETCH
    lexical_hits = engine.lexical.search(query, scope=scope, k=fetch)

    semantic_hits: List[Candidate] = []
    if engine.semantic is not None and gateway is not None:
        embedding = _embed_query(query, gateway)
        if embedding:
            semantic_hits = engine.semantic.search(embedding, scope=scope, k=fetch)

    # Primary fusion (lexical + semantic) seeds the relational expansion (P4):
    # a learning that shares a concept-entity with a confident hit surfaces via
    # the graph vote even when it matched no query term.
    primary = [[c.page_id for c in lexical_hits], [c.page_id for c in semantic_hits]]
    relational_hits: List[Candidate] = []
    if engine.relational is not None:
        seeds = rrf_fuse(primary)[:_RELATIONAL_SEEDS]
        if seeds:
            relational_hits = engine.relational.search(seeds, scope=scope, k=fetch)

    rankings = primary + [[c.page_id for c in relational_hits]]
    scores = _rrf_scores(rankings)
    fused_order = rrf_fuse(rankings)

    # Display fields per page. Lexical wins slug/page_type/snippet over semantic
    # over relational; snippet falls through non-empty in that priority.
    info: Dict[int, Candidate] = {c.page_id: c for c in relational_hits}
    info.update({c.page_id: c for c in semantic_hits})
    info.update({c.page_id: c for c in lexical_hits})
    lex_snip = {c.page_id: c.snippet for c in lexical_hits}
    sem_snip = {c.page_id: c.snippet for c in semantic_hits}
    rel_snip = {c.page_id: c.snippet for c in relational_hits}

    out: List[Candidate] = []
    for page_id in fused_order[:k]:
        c = info[page_id]
        out.append(Candidate(
            page_id=page_id, slug=c.slug, page_type=c.page_type,
            score=scores[page_id],
            snippet=(lex_snip.get(page_id) or sem_snip.get(page_id)
                     or rel_snip.get(page_id) or ""),
        ))
    return out


def _embed_query(query: str, gateway: object) -> List[float]:
    """One query → one embedding, or `[]` on any gateway failure. A read-path
    embedding failure (provider went down between bundle build and this call)
    must degrade to lexical-only, never raise — same posture as P2's reindex."""
    try:
        vectors = gateway.embed([query])          # type: ignore[attr-defined]
    except Exception:                             # provider down / timeout / bad dim
        return []
    return list(vectors[0]) if vectors else []


# ── construction (wire the real backends from config) ────────────────────────
#
# This factory lives here, not in `engine/__init__.py`, on purpose: that package
# is the pure contract layer ("depends only on the Protocols, never on a concrete
# backend"). Assembling the concrete searchers + the embedding gateway from
# config is the orchestration layer's job — the same layer that runs them.

@dataclass
class ResolverContext:
    """Everything `resolve()` needs, wired from config in one place: the engine
    bundle and the query-embedding gateway. `gateway` is None when embeddings are
    disabled or unavailable (then `engine.semantic` is None too → lexical-only).

    Owns the `VecStore`'s sidecar connection — call `close()` when done (the main
    DB connection is the caller's to close, as everywhere else)."""

    engine: RetrievalEngine
    gateway: Optional[object] = None
    _store: Optional[object] = None

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None


def build_context(conn: sqlite3.Connection) -> ResolverContext:
    """Assemble a `ResolverContext` over an open main-DB connection.

    Lexical always wires. Semantic wires only when BOTH a read-path gateway
    resolves (embeddings enabled, not `ATELIER_EMBED=off`) AND the sqlite-vec
    sidecar opens — otherwise the context is lexical-only. The gateway is built
    `warmup=False` (no per-call provider ping) and the `VecStore` is opened with
    that gateway's own signature/dim, so the read index can never silently
    diverge from what the write path (reindex) embedded.

    PERF FOLLOW-UP (P3, deferred): every call re-reads config, opens the
    `vectors.db` sidecar, and (in resolve) makes one provider embed call. On the
    per-`UserPromptSubmit` recall hook with embeddings on, that is real per-turn
    latency. It degrades gracefully and is fine for the hook's subprocess model
    (nothing to cache across calls), but the `atelier serve` daemon path
    (`db.connect_shared`) could memoize the context/store for its process
    lifetime. Out of P3 scope; optimize there if recall latency becomes a concern.
    """
    from ..ai import gateway as _gw
    from ..util import config as _config
    from .engine import FtsLexical, LinkRelational, VecSemantic, VecStore

    lexical = FtsLexical(conn)
    gateway = _gw.from_config(_gw.settings_from(_config.load().raw), warmup=False)

    semantic = None
    store = None
    if gateway is not None:
        store = VecStore.open(gateway_signature=gateway.signature, dim=gateway.dim)
        if store is not None:
            semantic = VecSemantic(conn, store)

    return ResolverContext(
        engine=RetrievalEngine(lexical=lexical, semantic=semantic,
                               relational=LinkRelational(conn)),
        gateway=gateway, _store=store,
    )
