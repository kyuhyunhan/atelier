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


def test_fts_sanitizes_punctuated_query(atelier_env):
    """A natural-language query with punctuation (hyphens, colons) must not crash
    FTS5 MATCH — the client PULL path has to be robust to real prompts."""
    from runtime.service import api
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "session-note.md",
        {"title": "Session Note", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# Session\n\nthe session-end auto-commit safety net catches the skip.\n",
    )
    api.reindex(space="gorae", full=True)
    # Would previously raise sqlite3.OperationalError: no such column: end
    hits = api.search("session-end auto-commit: safety-net!", space="gorae")
    assert any("session-note" in h["slug"] for h in hits)


def test_fts_dedups_multi_chunk_pages(atelier_env):
    """A page with several matching chunks must appear ONCE, not once per chunk."""
    from runtime.service import api
    body = "# Doc\n\n" + "\n\n".join(
        f"paragraph {i} mentions widget repeatedly." for i in range(6))
    write_page(
        atelier_env["gorae"] / "wiki" / "entities" / "widgety.md",
        {"title": "Widgety", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        body,
    )
    api.reindex(space="gorae", full=True)
    hits = api.search("widget", space="gorae")
    slugs = [h["slug"] for h in hits]
    assert slugs.count("wiki/entities/widgety.md") == 1   # de-duplicated by page


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
