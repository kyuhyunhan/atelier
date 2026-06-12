"""RFC 0002 P3 — the hybrid resolver.

Two layers, tested separately:
  rrf_fuse   pure rank-fusion math (no DB, no engine) — this file's first block.
  resolve    orchestration over the engine bundle — second block (added in step 2).

`rrf_fuse` is kept pure precisely so the fusion contract can be pinned without a
vault: it takes per-mode ranked id lists and returns one fused order. Everything
backend-specific (modes, scope, rehydration) lives above it in `resolve`.
"""
from __future__ import annotations

from typing import List, Sequence

from runtime.search.engine import Candidate, FtsLexical, RetrievalEngine, Scope
from runtime.search.engine.lexical import LexicalSearcher
from runtime.search.resolver import C_RRF, build_context, resolve, rrf_fuse
from runtime.util import db
from tests.conftest import write_page


def test_rrf_fuse_orders_by_summed_reciprocal_rank():
    # id1: 1/60 + 1/61   id3: 1/62 + 1/60   id2: 1/61   id4: 1/62
    # → 1 (both lists, top-ish) > 3 (both lists) > 2 (one list, rank0) > 4
    assert rrf_fuse([[1, 2, 3], [3, 1, 4]]) == [1, 3, 2, 4]


def test_rrf_fuse_empty_input_is_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_rrf_fuse_single_list_is_identity_order():
    # One mode, no fusion to do: the list passes through in its own order.
    assert rrf_fuse([[5, 6, 7]]) == [5, 6, 7]


def test_rrf_fuse_ties_break_stably_by_first_appearance():
    # id1 and id2 get identical summed scores (1/60 + 1/61 each). The tie must
    # break by first appearance across the inputs (id1 seen at list0[0]).
    assert rrf_fuse([[1, 2], [2, 1]]) == [1, 2]


def test_rrf_fuse_constant_is_the_standard_60():
    # C is the rank-smoothing constant (Cormack et al.). Pin it so a later tweak
    # is a deliberate, reviewed change — fusion ordering depends on it.
    assert C_RRF == 60


def test_rrf_fuse_rank0_in_two_modes_beats_rank0_in_one():
    # The core fusion property: agreement across modes outranks a lone strong hit.
    # id9 is rank0 in both lists; id1 is rank0 in only the first.
    fused = rrf_fuse([[1, 9], [9, 2]])
    assert fused[0] == 9


# ── resolve() over the engine bundle ─────────────────────────────────────────

class _FakeSemantic:
    """A SemanticSearcher that returns a fixed Candidate list, ignoring the
    embedding. Lets us fuse a *known* semantic vote against the real lexical
    mode without a live Ollama/sqlite-vec."""

    def __init__(self, hits: List[Candidate]) -> None:
        self._hits = hits

    def search(self, embedding: Sequence[float], *, scope: Scope = Scope(),
               k: int = 10) -> List[Candidate]:
        return self._hits[:k]


class _FakeGateway:
    """Returns one fixed-length vector per text. resolve only needs it to be
    truthy and non-raising — the fake semantic ignores the values."""

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _BrokenGateway:
    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        raise RuntimeError("provider down")


def _seed(atelier_env):
    """Two indexed pages: 'widget-note' matches the lexical word 'caching';
    'ephemeral-store' does NOT — only a semantic vote can surface it."""
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "widget-note.md",
        {"title": "Widget Note", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# Widget\n\nthe widget caching layer avoids recompute.\n",
    )
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "ephemeral-store.md",
        {"title": "Ephemeral Store", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# Ephemeral\n\na transient buffer discarded every turn.\n",
    )
    from runtime.service import api
    api.reindex(space="gorae", full=True)


def _page_id(conn, slug_like: str) -> int:
    row = conn.execute(
        "SELECT id, slug, page_type FROM pages WHERE slug LIKE ?",
        (f"%{slug_like}%",)).fetchone()
    return row["id"], row["slug"], row["page_type"]


def test_resolve_lexical_only_when_semantic_unwired(atelier_env):
    _seed(atelier_env)
    conn = db.connect()
    try:
        eng = RetrievalEngine(lexical=FtsLexical(conn))   # semantic=None
        hits = resolve("caching", engine=eng, scope=Scope(space="gorae"), k=5)
    finally:
        conn.close()
    assert hits, "lexical-only resolve should still find the body word"
    assert "widget-note" in hits[0].slug
    assert hits[0].score > 0, "fused score is a positive RRF magnitude"


def test_resolve_fuses_semantic_only_page_into_results(atelier_env):
    _seed(atelier_env)
    conn = db.connect()
    try:
        eid, eslug, etype = _page_id(conn, "ephemeral-store")
        wid, wslug, wtype = _page_id(conn, "widget-note")
        # Semantic returns both pages; widget also matches lexically → fuses to
        # the top, while ephemeral (no lexical match) rides in purely on its
        # semantic vote — the dark-learnings recovery this whole RFC is about.
        sem = _FakeSemantic([
            Candidate(page_id=wid, slug=wslug, page_type=wtype,
                      score=0.1, snippet="SEM_WIDGET"),
            Candidate(page_id=eid, slug=eslug, page_type=etype,
                      score=0.2, snippet="SEM_EPHEMERAL"),
        ])
        eng = RetrievalEngine(lexical=FtsLexical(conn), semantic=sem)
        hits = resolve("caching", engine=eng, scope=Scope(space="gorae"),
                       gateway=_FakeGateway(), k=5)
    finally:
        conn.close()
    slugs = [h.slug for h in hits]
    assert any("widget-note" in s for s in slugs)
    assert any("ephemeral-store" in s for s in slugs), \
        "a page with no lexical match must surface on its semantic vote alone"
    # widget is rank0 in BOTH modes → fuses above the semantic-only ephemeral.
    assert "widget-note" in hits[0].slug


def test_resolve_prefers_lexical_highlighted_snippet(atelier_env):
    _seed(atelier_env)
    conn = db.connect()
    try:
        wid, wslug, wtype = _page_id(conn, "widget-note")
        sem = _FakeSemantic([Candidate(page_id=wid, slug=wslug, page_type=wtype,
                                       score=0.1, snippet="SEM_WIDGET")])
        eng = RetrievalEngine(lexical=FtsLexical(conn), semantic=sem)
        hits = resolve("caching", engine=eng, scope=Scope(space="gorae"),
                       gateway=_FakeGateway(), k=5)
    finally:
        conn.close()
    widget = next(h for h in hits if "widget-note" in h.slug)
    assert "caching" in widget.snippet, "lexical highlighted snippet should win"
    assert widget.snippet != "SEM_WIDGET"


def test_resolve_degrades_to_lexical_when_gateway_raises(atelier_env):
    _seed(atelier_env)
    conn = db.connect()
    try:
        wid, wslug, wtype = _page_id(conn, "widget-note")
        sem = _FakeSemantic([Candidate(page_id=wid, slug=wslug, page_type=wtype,
                                       score=0.1, snippet="SEM")])
        eng = RetrievalEngine(lexical=FtsLexical(conn), semantic=sem)
        # Gateway raises → resolve must fall back to lexical-only, not propagate.
        hits = resolve("caching", engine=eng, scope=Scope(space="gorae"),
                       gateway=_BrokenGateway(), k=5)
    finally:
        conn.close()
    assert hits and "widget-note" in hits[0].slug


# ── build_context() wiring ───────────────────────────────────────────────────

def test_build_context_lexical_only_when_embeddings_off(atelier_env):
    """conftest pins ATELIER_EMBED=off → no gateway → semantic slot None. The
    factory must still wire lexical and produce a usable lexical-only context —
    this is the CI / no-Ollama path every other phase runs under."""
    _seed(atelier_env)
    conn = db.connect()
    ctx = build_context(conn)
    try:
        assert isinstance(ctx.engine.lexical, LexicalSearcher)
        assert ctx.engine.semantic is None
        assert ctx.gateway is None
        hits = resolve("caching", engine=ctx.engine, scope=Scope(space="gorae"),
                       gateway=ctx.gateway, k=5)
        assert hits and "widget-note" in hits[0].slug
    finally:
        ctx.close()
        conn.close()


# ── RFC 0002 P4 (revived by RFC 0003): relational mode surfaces concept-siblings ──

_LEARN_BASE = {
    "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "feedback",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
    "provenance": "learning", "sensitivity": "public",
}


def test_relational_surfaces_a_concept_sibling(atelier_env):
    """A learning that shares a concept-entity with a strong lexical hit, but
    matches NO query term itself, surfaces via the relational graph vote —
    learning A → entity → sibling B (2 hops). This is the dead-P4 revival the
    RFC 0003 stub backfill enables."""
    vault = atelier_env["gorae"]
    # The shared entity (basename normalizes to the concept the learnings touch).
    write_page(vault / "wiki" / "entities" / "widgetry.md",
               {"title": "Widgetry", "type": "entity", "category": "concept",
                "first_mention": "2026-01", "source_count": 0,
                "created": "2026-05-01", "updated": "2026-05-01",
                "provenance": "knowledge", "sensitivity": "public",
                "aliases": ["widgetry"]},
               "# Widgetry\n\nthe concept.\n")
    # A matches the query lexically AND touches widgetry.
    write_page(vault / "learnings" / "notes" / "2026-01" / "aa.md",
               {**_LEARN_BASE, "entry_id": "aa", "target_topic": "arch",
                "touches": ["widgetry"]},
               "## Observation\n\nkafka rebalance storms under load.\n")
    # B touches widgetry but its body shares NO word with the query.
    write_page(vault / "learnings" / "notes" / "2026-01" / "bb.md",
               {**_LEARN_BASE, "entry_id": "bb", "target_topic": "arch",
                "touches": ["widgetry"]},
               "## Observation\n\nalpha beta gamma delta.\n")
    from runtime.service import api
    api.reindex(space="gorae", full=True)

    conn = db.connect()
    ctx = build_context(conn)
    try:
        hits = resolve("kafka rebalance", engine=ctx.engine,
                       scope=Scope(page_types=("learning_accepted",)),
                       gateway=ctx.gateway, k=10)
    finally:
        ctx.close()
        conn.close()
    slugs = [h.slug for h in hits]
    assert any("aa" in s for s in slugs), "the lexical hit must be present"
    assert any("bb" in s for s in slugs), \
        "the concept-sibling must surface via the relational vote"
