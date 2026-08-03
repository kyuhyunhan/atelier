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
    links = extract_links(body, default_space="wiki")
    assert len(links) == 2
    bare = next(l for l in links if l.link_type == "wikilink")
    scoped = next(l for l in links if l.link_type == "workshop")
    assert bare.to_slug == "themes/example"
    assert scoped.to_space == "workshop"


def test_full_reindex_end_to_end(atelier_env):
    """Write 2 wiki pages with a wikilink, reindex, verify rows + link resolved."""
    from runtime.service import api
    from runtime.util import db

    wiki = atelier_env["wiki"]
    write_page(
        wiki / "wiki" / "themes" / "example.md",
        {"title": "example-theme", "type": "theme", "scope": "personal",
         "source_count": 0, "created": "2026-05-27", "updated": "2026-05-27"},
        "# example-theme\n\ncf. [[entities/foo]]\n",
    )
    write_page(
        wiki / "wiki" / "entities" / "foo.md",
        {"title": "foo", "type": "entity", "category": "concept",
         "first_mention": "2026-05", "source_count": 0,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# foo\n\nrelated to [[themes/example]]\n",
    )

    statses = api.reindex(space="wiki", full=True)
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
    assert classify("wiki", "wiki/digests/2026-05.md", {}) == "digest"
    assert classify("wiki", "wiki/entities/foo.md", {}) == "entity"
    assert classify("wiki", "raw/personal/diary/2026/05/15.md", {}) == "raw_source"
    assert classify("workshop", "products/foo/README.md", {}) == "product_readme"
    assert classify("workshop", "products/foo/adr/0001-bar.md", {}) == "product_page"


def test_learning_concept_edges_connect_cross_project(vault_env):
    """Phase 1: a learning becomes a node in the *concept* graph. Two accepted
    learnings in different projects that share a concept (`touches`) must both
    emit a `link_type='concept'` edge to that concept, so the corpus connects
    by idea, not by folder. Deterministic — no LLM, derived from frontmatter."""
    from runtime.service import api
    from runtime.util import db
    from tests.conftest import write_page

    vault = vault_env["vault"]
    base = {
        "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
        "ac_status": "passed", "observation_kind": "feedback",
        "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
    }
    write_page(
        vault / "learnings" / "notes" / "2026-01" / "a.md",
        {**base, "entry_id": "aaaa", "target_topic": "architecture",
         "target_project": "lexio", "touches": ["dependency-direction"]},
        "## Observation\n\ndepend on protocols, not implementations\n",
    )
    write_page(
        vault / "learnings" / "notes" / "2026-01" / "b.md",
        {**base, "entry_id": "bbbb", "target_topic": "layering",
         "target_project": "app", "touches": ["dependency-direction"]},
        "## Observation\n\ndependencies point inward\n",
    )

    api.reindex(full=True)

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT p.slug FROM links l JOIN pages p ON p.id = l.from_page "
            "WHERE l.link_type = 'concept' AND l.to_target = ?",
            ("dependency-direction",),
        ).fetchall()
        slugs = {r["slug"] for r in rows}
        assert any(s.endswith("a.md") for s in slugs), slugs
        assert any(s.endswith("b.md") for s in slugs), slugs
    finally:
        conn.close()


# ── RFC 0003 P1: _candidate_slugs accepts graph/ + provenance/ (rename targets) ──

def test_candidate_slugs_resolves_bare_entity_to_both_trees():
    """A bare `[[entities/foo]]` must offer BOTH the new graph/ home and the
    legacy wiki/ path, so entity links resolve before AND after the GP1 rename."""
    from runtime.index.reindex import _candidate_slugs
    cands = _candidate_slugs("entities/foo")
    assert "graph/entities/foo.md" in cands   # post-rename home
    assert "wiki/entities/foo.md" in cands    # legacy, during transition
    # graph/ is tried before wiki/ (post-rename is the canonical target)
    assert cands.index("graph/entities/foo.md") < cands.index("wiki/entities/foo.md")


# ── G5: collision-safe basename fallback for bare [[basename]] wikilinks ──

def test_bare_basename_resolves_to_deep_path_source(atelier_env):
    """A bare `[[2-months-more]]` (the shape graph/index.md emits ~275 times)
    must bind to a page whose slug is a full space-relative path
    (raw/personal/writings/2-months-more.md) that `_candidate_slugs` never
    probes — via the unambiguous-basename fallback."""
    from runtime.service import api
    from runtime.util import db

    wiki = atelier_env["wiki"]
    write_page(
        wiki / "raw" / "personal" / "writings" / "2-months-more.md",
        {"title": "2-months-more", "type": "raw_source",
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# 2-months-more\n\nbody\n",
    )
    write_page(
        wiki / "graph" / "index.md",
        {"title": "wiki-index", "type": "index",
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# index\n\n- [[2-months-more]]\n",
    )

    api.reindex(space="wiki", full=True)

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT l.to_page_id AS tid, p.slug AS slug "
            "FROM links l JOIN pages p ON p.id = l.to_page_id "
            "WHERE l.to_target = ?", ("2-months-more",),
        ).fetchone()
        assert row is not None, "bare basename link did not resolve"
        assert row["slug"] == "raw/personal/writings/2-months-more.md"
        broken = conn.execute("SELECT COUNT(*) AS n FROM broken_links").fetchone()["n"]
        assert broken == 0
    finally:
        conn.close()


def test_ambiguous_basename_does_not_resolve(atelier_env):
    """A basename owned by >1 page is NEVER guessed: the fallback drops it, so a
    bare `[[dup]]` stays dangling (to_page_id NULL) rather than binding to an
    arbitrary page."""
    from runtime.service import api
    from runtime.util import db

    wiki = atelier_env["wiki"]
    for sub in ("personal/writings", "personal/diary"):
        write_page(
            wiki / "raw" / sub.split("/")[0] / sub.split("/")[1] / "dup.md",
            {"title": "dup", "type": "raw_source",
             "created": "2026-05-27", "updated": "2026-05-27"},
            "# dup\n\nbody\n",
        )
    write_page(
        wiki / "graph" / "index.md",
        {"title": "wiki-index", "type": "index",
         "created": "2026-05-27", "updated": "2026-05-27"},
        "# index\n\n- [[dup]]\n",
    )

    api.reindex(space="wiki", full=True)

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT to_page_id AS tid FROM links WHERE to_target = ?", ("dup",),
        ).fetchone()
        assert row is not None, "the [[dup]] link row should exist"
        assert row["tid"] is None, "an ambiguous basename must NOT be guessed"
    finally:
        conn.close()


# ── G6: session-id fallback for timestamp-identity migration scars (RFC 0009) ──

def _write_claim(vault, entry_id, body, *, session_id=None):
    """A v7 atomic claim (classify keys off schema_version>=7 + kind=claim, so
    the path is incidental). `session_id` is the OLD learning-note timestamp
    identity the RFC 0005 migration stamped onto the frontmatter."""
    fm = {"entry_id": entry_id, "schema_version": 7, "kind": "claim",
          "statement": f"claim {entry_id}", "derived_from": [],
          "surfacing": "query", "domain": "operational",
          "sensitivity": "private",
          "created": "2026-05-27", "updated": "2026-05-27"}
    if session_id is not None:
        fm["session_id"] = session_id
    write_page(vault / "graph" / "atomic" / f"{entry_id}.md", fm, body)


def test_bare_timestamp_wikilink_resolves_via_session_id(atelier_env):
    """A sibling claim referencing a migrated learning by its OLD bare timestamp
    identity `[[20260514T0530]]` must bind to the content-hash-slugged claim
    whose frontmatter carries `session_id: 20260514T0530`."""
    from runtime.service import api
    from runtime.util import db

    wiki = atelier_env["wiki"]
    _write_claim(wiki, "c0ffee", "## Observation\n\nthe target.\n",
                 session_id="20260514T0530")
    _write_claim(wiki, "beef00",
                 "## Observation\n\nrefines [[20260514T0530]].\n")

    api.reindex(space="wiki", full=True)

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT p.slug AS slug FROM links l JOIN pages p ON p.id = l.to_page_id "
            "WHERE l.to_target = ?", ("20260514T0530",),
        ).fetchone()
        assert row is not None, "bare-timestamp link did not resolve"
        assert row["slug"] == "graph/atomic/c0ffee.md"
    finally:
        conn.close()


def test_path_form_provenance_timestamp_resolves_via_session_id(atelier_env):
    """The path-form scar `[[provenance/learning/notes/2026-05/20260514T0530-slug.md]]`
    (no such page exists post-migration) resolves to the same claim by the
    \\d{8}T\\d{4} stamp extracted from its basename prefix."""
    from runtime.service import api
    from runtime.util import db

    wiki = atelier_env["wiki"]
    _write_claim(wiki, "c0ffee", "## Observation\n\nthe target.\n",
                 session_id="20260514T0530")
    link = "[[provenance/learning/notes/2026-05/20260514T0530-some-slug.md]]"
    _write_claim(wiki, "beef00", f"## Observation\n\nrefines {link}.\n")

    api.reindex(space="wiki", full=True)

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT p.slug AS slug FROM links l JOIN pages p ON p.id = l.to_page_id "
            "WHERE l.to_target = ?",
            ("provenance/learning/notes/2026-05/20260514T0530-some-slug.md",),
        ).fetchone()
        assert row is not None, "path-form timestamp link did not resolve"
        assert row["slug"] == "graph/atomic/c0ffee.md"
    finally:
        conn.close()


def test_ambiguous_or_absent_session_id_stays_null(atelier_env):
    """Collision-safe: a session_id owned by >1 claim is DROPPED from the index
    (never guessed), and a stamp no claim owns has no entry — both leave the
    body wikilink dangling (to_page_id NULL) rather than binding arbitrarily."""
    from runtime.service import api
    from runtime.util import db

    wiki = atelier_env["wiki"]
    # Two claims share the SAME session_id → ambiguous → dropped.
    _write_claim(wiki, "aaaa11", "## Observation\n\ndup a.\n",
                 session_id="20260514T0530")
    _write_claim(wiki, "bbbb22", "## Observation\n\ndup b.\n",
                 session_id="20260514T0530")
    # Referencer points at the ambiguous stamp AND an orphan stamp no claim owns.
    _write_claim(
        wiki, "cccc33",
        "## Observation\n\nsee [[20260514T0530]] and [[20991231T2359]].\n")

    api.reindex(space="wiki", full=True)

    conn = db.connect()
    try:
        for target in ("20260514T0530", "20991231T2359"):
            row = conn.execute(
                "SELECT to_page_id AS tid FROM links WHERE to_target = ?",
                (target,),
            ).fetchone()
            assert row is not None, f"the [[{target}]] link row should exist"
            assert row["tid"] is None, (
                f"[[{target}]] must NOT bind (ambiguous/absent session_id)")
    finally:
        conn.close()


def test_candidate_slugs_treats_rename_prefixes_as_exact():
    """A slug already under a known prefix (incl. the new graph/ and provenance/)
    is an exact path, never re-expanded under graph//wiki/."""
    from runtime.index.reindex import _candidate_slugs
    # graph/ and provenance/ are known prefixes (not re-expanded under shorthand),
    # but DO alias to their old names so links resolve across the rename.
    g = _candidate_slugs("graph/entities/foo.md")
    assert g[0] == "graph/entities/foo.md" and "wiki/entities/foo.md" in g
    pv = _candidate_slugs("provenance/personal/x.md")
    assert pv[0] == "provenance/personal/x.md" and "raw/personal/x.md" in pv
