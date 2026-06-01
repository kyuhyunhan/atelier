"""Regression coverage for the v0.2 gorae→vault single-vault rename.

Locks in: schema-driven (space-independent) classification, space-agnostic
lint, D2 phantom-drift dedup, cross-domain + alias link resolution, and the
learnings by-project mirror reconcile (D7).
"""
from __future__ import annotations

from typing import Dict

from .conftest import write_page


# ── 1.1 schema-driven classify (pure; no fixture) ───────────────────────────

def test_classify_schema_driven_both_layouts() -> None:
    from runtime.index.classify import classify

    cases = {
        # single-vault slugs
        "wiki/entities/foo.md": "entity",
        "wiki/synthesis/x.md": "synthesis",
        "wiki/index.md": "wiki_index",
        "raw/personal/diary/2026/05/15.md": "raw_source",
        "workshop/products/demo/README.md": "product_readme",
        "workshop/products/demo/spec/x.md": "product_page",
        "learnings/candidates/2026-06/x.md": "learning_candidate",
        "learnings/accepted/by-topic/t/x.md": "learning_accepted",
        "learnings/principles/INDEX.md": "learnings_index",   # NOT a principle
        "learnings/principles/p.md": "learning_principle",
        # legacy two-space slug (back-compat)
        "products/demo/README.md": "product_readme",
    }
    for slug, expected in cases.items():
        for space in ("gorae", "vault-builder", "workshop", ""):
            assert classify(space, slug, {}) == expected, (slug, space)


def test_classify_digest_glob() -> None:
    """Overlay declares digests as YYYY-MM; classification treats it as a glob."""
    from runtime.index.classify import classify
    assert classify("vault-builder", "wiki/digests/2026-05.md", {}) == "digest"


# ── 1.2/1.5 lint space-agnostic + D2 dedup (single vault) ───────────────────

def _reindex(cfg) -> None:
    from runtime.index import reindex
    reindex.reindex_all(cfg, full=True)


def test_d2_dedup_single_vault_no_phantom_drift(vault_env: Dict) -> None:
    from runtime.util import config
    from runtime.doctor import diagnostics

    vault = vault_env["vault"]
    write_page(vault / "wiki" / "entities" / "foo.md",
               {"title": "Foo", "type": "entity"}, "Body.")
    cfg = config.load()
    _reindex(cfg)

    d2 = next(d for d in diagnostics.run_all(cfg) if d.id == "D2")
    assert d2.severity == "OK", d2.message


def test_lint_space_agnostic_single_vault(vault_env: Dict) -> None:
    from runtime.util import config, db
    from runtime.lint import runner

    vault = vault_env["vault"]
    # a wiki page with a broken [[raw/...]] link → L1 should fire space-agnostically
    write_page(vault / "wiki" / "sources" / "s.md",
               {"title": "S", "type": "source"},
               "See [[raw/personal/missing.md]].")
    cfg = config.load()
    _reindex(cfg)

    conn = db.connect()
    try:
        report = runner.run(conn)  # space=None → production single-vault path
    finally:
        conn.close()
    assert "L1" in report.rules_run
    assert any(f.rule_id == "L1" for f in report.findings)


# ── 2.1/2.2 cross-domain + alias resolution ─────────────────────────────────

def _links_from(conn, slug_like: str):
    return conn.execute(
        "SELECT l.to_target, l.to_page_id FROM links l "
        "JOIN pages p ON p.id = l.from_page WHERE p.slug = ?",
        (slug_like,)).fetchall()


def test_cross_domain_resolve_workshop_to_wiki_entity(vault_env: Dict) -> None:
    from runtime.util import config, db

    vault = vault_env["vault"]
    write_page(vault / "wiki" / "entities" / "shinto.md",
               {"title": "Shinto", "type": "entity"}, "An entity.")
    # a workshop page (different domain) references the entity by bare basename
    write_page(vault / "workshop" / "products" / "demo" / "note.md",
               {"title": "Note", "type": "note"},
               "Relates to [[shinto]] and [[entities/shinto]].")
    cfg = config.load()
    _reindex(cfg)

    conn = db.connect()
    try:
        ent_id = conn.execute(
            "SELECT id FROM pages WHERE slug='wiki/entities/shinto.md'"
        ).fetchone()["id"]
        rows = _links_from(conn, "workshop/products/demo/note.md")
        targets = {r["to_target"]: r["to_page_id"] for r in rows}
        assert targets["shinto"] == ent_id           # alias/basename resolution
        assert targets["entities/shinto"] == ent_id   # shorthand candidate
    finally:
        conn.close()


def test_alias_resolution(vault_env: Dict) -> None:
    from runtime.util import config, db

    vault = vault_env["vault"]
    write_page(vault / "wiki" / "entities" / "bar.md",
               {"title": "Bar", "type": "entity", "aliases": ["Bar Alias"]},
               "Entity with an alias.")
    write_page(vault / "learnings" / "accepted" / "by-topic" / "t" / "l.md",
               {"schema_version": 4, "entry_id": "x", "status": "accepted",
                "target_topic": "t"},
               "Mentions [[Bar Alias]].")
    cfg = config.load()
    _reindex(cfg)

    conn = db.connect()
    try:
        ent_id = conn.execute(
            "SELECT id FROM pages WHERE slug='wiki/entities/bar.md'"
        ).fetchone()["id"]
        rows = _links_from(conn, "learnings/accepted/by-topic/t/l.md")
        assert any(r["to_page_id"] == ent_id for r in rows), rows
    finally:
        conn.close()


# ── 3.2 learnings mirror reconcile (D7) ─────────────────────────────────────

def test_learnings_mirror_reconcile(vault_env: Dict) -> None:
    from runtime.service.learnings import reconcile

    vault = vault_env["vault"]
    acc = vault / "learnings" / "accepted"
    fm = {"schema_version": 4, "entry_id": "e1", "status": "accepted",
          "ac_status": "passed", "target_topic": "t", "target_project": "proj"}
    # canonical with a project → expects exactly one mirror
    write_page(acc / "by-topic" / "t" / "note.md", fm, "Lesson body.")
    # but we seed a DUPLICATE mirror and NO correct one is missing here…
    write_page(acc / "by-project" / "proj" / "note.md", fm, "Lesson body.")
    write_page(acc / "by-project" / "proj" / "note-1.md", fm, "Lesson body.")
    # …plus an orphan mirror whose canonical doesn't exist
    write_page(acc / "by-project" / "proj" / "ghost.md",
               {**fm, "entry_id": "gone"}, "Orphan.")

    drifts = reconcile.check(vault)
    kinds = sorted(d.kind for d in drifts)
    assert "duplicate" in kinds   # note-1.md
    assert "orphan" in kinds      # ghost.md

    counts = reconcile.repair(vault)
    assert counts["duplicate_removed"] >= 1
    assert counts["orphan_removed"] >= 1
    # after repair: clean
    assert reconcile.check(vault) == []
    # the canonical-named mirror survives; the duplicate is gone
    assert (acc / "by-project" / "proj" / "note.md").exists()
    assert not (acc / "by-project" / "proj" / "note-1.md").exists()
    assert not (acc / "by-project" / "proj" / "ghost.md").exists()
