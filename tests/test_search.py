"""FTS search and graph traversal."""
from __future__ import annotations

from tests.conftest import write_page


def test_fts_returns_hits(atelier_env):
    from runtime.service import api

    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "example-person.md",
        {"title": "Example Person", "type": "entity", "category": "person",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# Example Person\n\nA placeholder entity used for indexer testing.\n",
    )
    api.reindex(space="gorae", full=True)

    hits = api.search("placeholder", space="gorae")
    assert any("example-person" in h["slug"].lower() for h in hits)


def test_graph_inbound_outbound(atelier_env):
    from runtime.service import api
    from runtime.search import graph
    from runtime.util import db

    gorae = atelier_env["gorae"]
    write_page(
        gorae / "wiki" / "themes" / "x.md",
        {"title": "x", "type": "theme", "scope": "personal", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "see [[entities/y]]",
    )
    write_page(
        gorae / "wiki" / "entities" / "y.md",
        {"title": "y", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "(y)",
    )
    api.reindex(space="gorae", full=True)

    conn = db.connect()
    try:
        ins = graph.inbound(conn, "wiki/entities/y.md")
        outs = graph.outbound(conn, "wiki/themes/x.md")
    finally:
        conn.close()
    assert "wiki/themes/x.md" in ins
    assert "wiki/entities/y.md" in outs
