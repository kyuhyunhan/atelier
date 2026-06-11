"""RFC 0002 P2 — the reindex embed pass (auto-when-reachable, always skippable).

Contract: reindex with a gateway populates the sidecar and reports stats;
reindex without one (provider down / ATELIER_EMBED=off / extension missing)
is byte-identical to today's behavior; unchanged content costs zero gateway
calls on re-runs.
"""
from __future__ import annotations

from typing import Dict

from runtime.index import reindex as _reindex
from runtime.util import config as _config
from tests.conftest import write_page
from tests.test_vecstore import CountingGateway


def _seed(atelier_env: Dict) -> None:
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "n.md",
        {"title": "N", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# N\n\nnote body words.\n",
    )


def test_reindex_with_gateway_embeds_and_reports(atelier_env):
    _seed(atelier_env)
    cfg = _config.load()
    gw = CountingGateway()
    stats = _reindex.reindex_space(cfg, "gorae", full=True, embed_gateway=gw)
    assert stats.chunks_embedded > 0
    assert stats.chunks_reused == 0
    assert len(gw.embedded) == stats.chunks_embedded


def test_second_reindex_reuses_cache_zero_gateway_calls(atelier_env):
    _seed(atelier_env)
    cfg = _config.load()
    gw = CountingGateway()
    _reindex.reindex_space(cfg, "gorae", full=True, embed_gateway=gw)
    calls_after_first = len(gw.embedded)

    stats2 = _reindex.reindex_space(cfg, "gorae", full=True, embed_gateway=gw)
    assert len(gw.embedded) == calls_after_first       # determinism guarantee
    assert stats2.chunks_embedded == 0
    assert stats2.chunks_reused > 0


def test_reindex_without_gateway_is_unchanged(atelier_env):
    """embed_gateway=None (or auto resolving to None) must leave reindex exactly
    as it was pre-P2 — stats zero, no sidecar dependency touched."""
    _seed(atelier_env)
    cfg = _config.load()
    stats = _reindex.reindex_space(cfg, "gorae", full=True, embed_gateway=None)
    assert stats.chunks_embedded == 0 and stats.chunks_reused == 0
    assert stats.pages_seen > 0                        # normal indexing happened


def test_auto_mode_disabled_by_env_kill_switch(atelier_env, monkeypatch):
    """With ATELIER_EMBED=off (the conftest default), auto resolution yields no
    gateway — proven here without any network attempt."""
    monkeypatch.setenv("ATELIER_EMBED", "off")
    from runtime.ai import gateway as gwmod
    settings = gwmod.settings_from({"embedding": {"enabled": True}})
    assert settings.enabled is False
    assert gwmod.from_config(settings) is None


def test_gateway_failure_midpass_does_not_abort_reindex(atelier_env):
    """A provider that dies mid-embed must NOT fail a reindex whose lexical
    passes already committed — 'optional semantic, never crash' applies to a
    provider that drops DURING the pass, not only one down before it starts."""
    _seed(atelier_env)
    cfg = _config.load()

    class BoomGateway:
        signature = "fake:boom:4:chunker_v1"
        dim = 4
        def embed(self, texts):
            raise OSError("provider died mid-pass")

    # Must not raise.
    stats = _reindex.reindex_space(cfg, "gorae", full=True, embed_gateway=BoomGateway())
    assert stats.pages_seen > 0           # lexical reindex completed
    assert stats.chunks_embedded == 0     # embed pass aborted cleanly

    from runtime.util import db
    conn = db.connect()
    try:
        n = conn.execute("SELECT COUNT(*) n FROM pages").fetchone()["n"]
    finally:
        conn.close()
    assert n > 0                          # pages queryable via the lexical index
