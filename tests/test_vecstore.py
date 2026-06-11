"""RFC 0002 P2 — vectors.db sidecar (embedding_cache + vec_chunks projection).

The contract under test:
- durable: `embedding_cache` is keyed by (content_hash, signature) and survives
  a projection rebuild — unchanged text NEVER re-hits the gateway;
- projection: `vec_chunks` is rebuilt from the main DB's chunks joined onto the
  cache, same disposable status as chunks_fts;
- stale detection: a signature change (new model) re-embeds everything;
- optional: without the sqlite-vec extension the store reports unavailable and
  callers skip — atelier stays lexical-only.
"""
from __future__ import annotations

from typing import Dict

import pytest

from runtime.search.engine import vecstore
from tests.conftest import write_page


class CountingGateway:
    """Deterministic fake: vector = [len(text), position-independent], counts calls."""

    def __init__(self, dim: int = 4, signature: str = "fake:m:4:chunker_v1"):
        self.dim = dim
        self._sig = signature
        self.embedded: list[str] = []

    @property
    def signature(self) -> str:
        return self._sig

    def embed(self, texts):
        self.embedded.extend(texts)
        return [[float(len(t))] * self.dim for t in texts]


@pytest.fixture
def indexed_env(atelier_env: Dict, monkeypatch):
    """Two indexed pages; returns (env, main_conn_factory)."""
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "a.md",
        {"title": "A", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# A\n\nalpha body text.\n",
    )
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "b.md",
        {"title": "B", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# B\n\nbeta body text, longer.\n",
    )
    from runtime.service import api
    api.reindex(space="gorae", full=True)
    return atelier_env


def test_sync_embeds_all_chunks_once(indexed_env):
    from runtime.util import db
    gw = CountingGateway()
    conn = db.connect()
    try:
        store = vecstore.VecStore.open(gateway_signature=gw.signature, dim=gw.dim)
        stats = store.sync(conn, gw)
        n_chunks = conn.execute("SELECT COUNT(*) n FROM chunks").fetchone()["n"]
    finally:
        conn.close()
    assert n_chunks > 0
    # embedded counts UNIQUE texts (deduped by hash); reused covers the rest.
    assert 0 < stats.embedded <= n_chunks
    assert stats.reused == n_chunks - stats.embedded
    assert store.count() == n_chunks            # every chunk gets a vec row
    store.close()


def test_resync_unchanged_content_hits_gateway_zero_times(indexed_env):
    """The determinism guarantee: rebuild reuses the cache; the gateway is not
    called for text that did not change (RFC 0002 §9)."""
    from runtime.util import db
    gw = CountingGateway()
    conn = db.connect()
    try:
        store = vecstore.VecStore.open(gateway_signature=gw.signature, dim=gw.dim)
        store.sync(conn, gw)
        first_calls = len(gw.embedded)
        stats2 = store.sync(conn, gw)            # nothing changed
    finally:
        conn.close()
    assert len(gw.embedded) == first_calls       # zero new gateway calls
    assert stats2.embedded == 0
    assert stats2.reused == store.count() > 0
    store.close()


def test_signature_change_re_embeds(indexed_env):
    from runtime.util import db
    conn = db.connect()
    try:
        g1 = CountingGateway(signature="fake:m1:4:chunker_v1")
        s1 = vecstore.VecStore.open(gateway_signature=g1.signature, dim=4)
        s1.sync(conn, g1)
        s1.close()
        # new model → new signature → full re-embed
        g2 = CountingGateway(signature="fake:m2:4:chunker_v1")
        s2 = vecstore.VecStore.open(gateway_signature=g2.signature, dim=4)
        stats = s2.sync(conn, g2)
    finally:
        conn.close()
    assert stats.embedded > 0 and stats.reused == 0
    s2.close()


def test_sync_persists_completed_batches_on_midway_failure(indexed_env):
    """Durability: streaming sync commits each batch, so a provider failure
    partway through leaves the completed batches cached — the next run resumes
    only the remainder instead of re-embedding everything (laptop-first: a long
    bulk pass must survive an interruption)."""
    from runtime.util import db

    class FailAfterFirstBatch(CountingGateway):
        def __init__(self):
            super().__init__()
            self._batches = 0
        def embed(self, texts):
            self._batches += 1
            if self._batches > 1:
                raise OSError("provider died mid-pass")
            return super().embed(texts)

    conn = db.connect()
    try:
        # force >1 batch so the second one fails
        store = vecstore.VecStore.open(gateway_signature="fake:m:4:chunker_v1", dim=4)
        gw = FailAfterFirstBatch()
        with pytest.raises(OSError):
            store.sync(conn, gw, commit_batch=1)
        cached = store._conn.execute(
            "SELECT COUNT(*) n FROM embedding_cache").fetchone()["n"]
    finally:
        conn.close()
    assert cached >= 1, "the first committed batch must survive the failure"
    store.close()


def test_knn_returns_nearest_chunk_ids(indexed_env):
    from runtime.util import db
    gw = CountingGateway()
    conn = db.connect()
    try:
        store = vecstore.VecStore.open(gateway_signature=gw.signature, dim=gw.dim)
        store.sync(conn, gw)
        # fake vectors are [len(text)]*4 — query at exactly the shortest chunk's
        # length. Several chunks can tie on length (the fake encodes only that),
        # so assert the PROPERTY: the top hit is at distance 0 and its text has
        # the queried length — not a specific chunk id.
        lengths = {r["id"]: len(r["text"])
                   for r in conn.execute("SELECT id, text FROM chunks")}
        shortest = min(lengths.values())
        hits = store.knn([float(shortest)] * 4, k=1)
    finally:
        conn.close()
    assert hits, "expected at least one kNN hit"
    top_id, top_dist = hits[0]
    assert top_dist == 0.0
    assert lengths[top_id] == shortest
    store.close()


def test_unavailable_without_extension(monkeypatch, atelier_env):
    monkeypatch.setattr(vecstore, "_load_sqlite_vec", lambda conn: False)
    assert vecstore.VecStore.open(gateway_signature="x", dim=4) is None
