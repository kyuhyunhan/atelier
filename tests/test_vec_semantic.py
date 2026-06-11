"""RFC 0002 P2 — VecSemantic fills the SemanticSearcher contract slot.

kNN hits from the sidecar come back as page-level Candidates (the engine's
shared vocabulary), scope-filtered and page-deduplicated — mirroring the
FtsLexical tests so the two modes prove the same contract behaviors.
"""
from __future__ import annotations

from typing import Dict

import pytest

from runtime.search.engine import RetrievalEngine, FtsLexical, VecSemantic, VecStore, Scope, Candidate
from runtime.search.engine.semantic import SemanticSearcher
from runtime.util import db
from tests.conftest import write_page
from tests.test_vecstore import CountingGateway


@pytest.fixture
def vec_env(atelier_env: Dict):
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "short.md",
        {"title": "Short", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# S\n\ntiny.\n",
    )
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "long.md",
        {"title": "Long", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# L\n\nthis is a deliberately much longer chunk of body text here.\n",
    )
    from runtime.service import api
    api.reindex(space="gorae", full=True)
    return atelier_env


def _synced(conn):
    gw = CountingGateway()
    store = VecStore.open(gateway_signature=gw.signature, dim=gw.dim)
    store.sync(conn, gw)
    return store


def test_vecsemantic_satisfies_protocol(vec_env):
    conn = db.connect()
    try:
        store = _synced(conn)
        assert isinstance(VecSemantic(conn, store), SemanticSearcher)
        store.close()
    finally:
        conn.close()


def test_search_returns_nearest_page_candidates(vec_env):
    """Fake vectors encode text length, so a query at the long chunk's length
    must surface the long page first — proving knn → page join → Candidate."""
    conn = db.connect()
    try:
        store = _synced(conn)
        target_len = max(len(r["text"]) for r in
                         conn.execute("SELECT text FROM chunks"))
        eng = VecSemantic(conn, store)
        hits = eng.search([float(target_len)] * 4, scope=Scope(space="gorae"), k=3)
        store.close()
    finally:
        conn.close()
    assert hits and isinstance(hits[0], Candidate)
    assert "long" in hits[0].slug
    assert hits[0].page_id > 0
    # page-level dedup: no slug twice
    slugs = [h.slug for h in hits]
    assert len(slugs) == len(set(slugs))


def test_scope_page_types_filters(vec_env):
    conn = db.connect()
    try:
        store = _synced(conn)
        eng = VecSemantic(conn, store)
        none = eng.search([5.0] * 4, scope=Scope(page_types=("learning_accepted",)), k=3)
        some = eng.search([5.0] * 4, scope=Scope(), k=3)
        store.close()
    finally:
        conn.close()
    assert none == []
    assert some


def test_empty_embedding_returns_empty(vec_env):
    conn = db.connect()
    try:
        store = _synced(conn)
        assert VecSemantic(conn, store).search([], k=3) == []
        store.close()
    finally:
        conn.close()


def test_bundle_semantic_slot_fills(vec_env):
    conn = db.connect()
    try:
        store = _synced(conn)
        eng = RetrievalEngine(lexical=FtsLexical(conn),
                              semantic=VecSemantic(conn, store))
        assert eng.semantic is not None
        assert eng.relational is None       # P4
        store.close()
    finally:
        conn.close()
