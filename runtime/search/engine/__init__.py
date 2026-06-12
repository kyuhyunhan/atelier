"""Retrieval engine — the pluggable substrate behind atelier's resolver (RFC 0002).

This package is the *contract layer* for retrieval. It is split one-file-per-mode
so the substrate's three responsibilities are visible at a glance:

    lexical.py     keyword / BM25            (LexicalSearcher    — FtsLexical, P0)
    semantic.py    vector kNN                (SemanticSearcher   — VecSemantic, P2)
    relational.py  graph traversal           (RelationalSearcher — impl P4)
    types.py       shared vocabulary         (Scope, Candidate)
    vecstore.py    vectors.db sidecar        (semantic write side + kNN index)

`RetrievalEngine` below is the higher-level view: a bundle holding *one searcher
of each mode*. The resolver (P3) depends on this bundle and on the three Protocols
— never on a concrete backend. Swapping a backend (e.g. SQLite vec0 → pgvector for
the semantic mode alone) means replacing one searcher in the bundle; the resolver
and the other two modes are untouched.

Today only the lexical mode has an implementation (`FtsLexical`); `semantic` and
`relational` are `None` until their phases land. That a contract exists before its
implementation is the point of P0 — the roadmap is encoded in the type system.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .lexical import FtsLexical, LexicalSearcher
from .relational import LinkRelational, RelationalSearcher
from .semantic import SemanticSearcher, VecSemantic
from .types import Candidate, Scope
from .vecstore import VecStore

__all__ = [
    "RetrievalEngine",
    "LexicalSearcher", "SemanticSearcher", "RelationalSearcher",
    "FtsLexical", "VecSemantic", "VecStore", "LinkRelational",
    "Scope", "Candidate",
]


@dataclass(frozen=True)
class RetrievalEngine:
    """One searcher per mode. `semantic`/`relational` are optional until their
    implementations land (P2/P4); a resolver checks for `None` and runs only the
    modes that are wired. This keeps every phase shippable on its own."""

    lexical: LexicalSearcher
    semantic: Optional[SemanticSearcher] = None
    relational: Optional[RelationalSearcher] = None
