"""Reindex pipeline: parse, classify, link resolution, entity detection."""
from __future__ import annotations

from tests.conftest import write_page


def test_parse_splits_frontmatter():
    from runtime.index.parse import split_frontmatter
    text = "---\ntitle: foo\nsensitivity: private\n---\nbody here\n"
    fm, body = split_frontmatter(text)
    assert fm["title"] == "foo"
    assert "body here" in body


def test_chunk_body_tracks_headings():
    from runtime.index.parse import chunk_body
    body = "# H1\n\nintro\n\n## H2\n\nsub\n"
    chunks = chunk_body(body)
    assert len(chunks) == 2
    assert chunks[0].heading_path == "H1"
    assert chunks[1].heading_path == "H1 > H2"


def test_linker_extracts_bare_and_scoped():
    from runtime.index.linker import extract_links
    body = "see [[themes/example]] and [[workshop:products/foo/README.md]]"
    links = extract_links(body, default_space="gorae")
    assert len(links) == 2
    bare = next(l for l in links if l.link_type == "wikilink")
    scoped = next(l for l in links if l.link_type == "workshop")
    assert bare.to_slug == "themes/example"
    assert scoped.to_space == "workshop"


def test_full_reindex_end_to_end(atelier_env):
    """Write 2 wiki pages with a wikilink, reindex, verify rows + link resolved."""
    from runtime.service import api
    from runtime.util import db

    gorae = atelier_env["gorae"]
    write_page(
        gorae / "wiki" / "themes" / "example.md",
        {"title": "example-theme", "type": "theme", "scope": "personal",
         "source_count": 0, "created": "2026-05-27", "updated": "2026-05-27"},
        "# example-theme\n\ncf. [[entities/foo]]\n",
    )
    write_page(
        gorae / "wiki" / "entities" / "foo.md",
        {"title": "foo", "type": "entity", "category": "concept",
         "first_mention": "2026-05", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# foo\n\nrelated to [[themes/example]]\n",
    )

    statses = api.reindex(space="gorae", full=True)
    assert statses[0]["pages_changed"] == 2

    conn = db.connect()
    try:
        broken = conn.execute("SELECT COUNT(*) AS n FROM broken_links").fetchone()["n"]
        assert broken == 0, "v3 shorthand should resolve themes/foo → wiki/themes/foo.md"
        n_entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        assert n_entities == 1
    finally:
        conn.close()


def test_classify_page_types():
    from runtime.index.classify import classify
    assert classify("gorae", "wiki/digests/2026-05.md", {}) == "digest"
    assert classify("gorae", "wiki/entities/foo.md", {}) == "entity"
    assert classify("gorae", "raw/personal/diary/2026/05/15.md", {}) == "raw_source"
    assert classify("workshop", "products/foo/README.md", {}) == "product_readme"
    assert classify("workshop", "products/foo/adr/0001-bar.md", {}) == "product_page"
