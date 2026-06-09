"""RFC 0002 P0 — the RetrievalEngine contract layer.

These tests do not change retrieval behavior; they assert that the new contract
(per-mode searchers + the bundle) fits the *real* backend, so the resolver (P3)
can depend on the Protocols instead of on fts.py directly.
"""
from __future__ import annotations

from runtime.search import engine
from runtime.search.engine import Candidate, FtsLexical, RetrievalEngine, Scope
from runtime.search.engine.lexical import LexicalSearcher
from runtime.util import db
from tests.conftest import write_page


def _seed_and_index(atelier_env):
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "widget-note.md",
        {"title": "Widget Note", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# Widget\n\nthe widget caching layer avoids recompute.\n",
    )
    from runtime.service import api
    api.reindex(space="gorae", full=True)


def test_ftslexical_satisfies_protocol(atelier_env):
    conn = db.connect()
    try:
        assert isinstance(FtsLexical(conn), LexicalSearcher)
    finally:
        conn.close()


def test_ftslexical_returns_candidates_with_page_id(atelier_env):
    _seed_and_index(atelier_env)
    conn = db.connect()
    try:
        hits = FtsLexical(conn).search("caching", scope=Scope(space="gorae"), k=5)
    finally:
        conn.close()
    assert hits, "expected a lexical hit for an indexed body word"
    assert all(isinstance(h, Candidate) for h in hits)
    top = hits[0]
    assert "widget-note" in top.slug
    assert top.page_id > 0           # the seam carries page_id (fts.Hit did not)
    assert top.page_type == "entity"


def test_scope_page_types_filters_out_nonmatching_types(atelier_env):
    _seed_and_index(atelier_env)
    conn = db.connect()
    try:
        eng = FtsLexical(conn)
        # The body word exists, but no page is a learning → page_type scope empties it.
        learning_only = eng.search("caching", scope=Scope(page_types=("learning_accepted",)), k=5)
        anything = eng.search("caching", scope=Scope(), k=5)
    finally:
        conn.close()
    assert anything, "unscoped search should still find the entity page"
    assert learning_only == [], "page_type scope must exclude non-learning pages"


def test_empty_query_returns_no_hits_never_raises(atelier_env):
    conn = db.connect()
    try:
        assert FtsLexical(conn).search("!!!", scope=Scope(), k=5) == []
        assert FtsLexical(conn).search("", scope=Scope(), k=5) == []
    finally:
        conn.close()


def test_bundle_holds_one_searcher_per_mode(atelier_env):
    conn = db.connect()
    try:
        eng = RetrievalEngine(lexical=FtsLexical(conn))
        assert isinstance(eng.lexical, LexicalSearcher)
        assert eng.semantic is None        # impl lands in P2
        assert eng.relational is None      # impl lands in P4
    finally:
        conn.close()
