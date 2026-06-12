"""RFC 0003 P3 — the deterministic entity-stub backfill (connect the orphan island)."""
from __future__ import annotations

from typing import Dict

from runtime.service import api
from runtime.service.learnings import entity_backfill as eb
from runtime.util import db
from tests.conftest import write_page


_BASE = {
    "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "feedback",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
    "provenance": "learning", "sensitivity": "public",
}


def _accepted(vault, entry_id, touches, body="## Observation\n\nfoo bar.\n"):
    fm = {**_BASE, "entry_id": entry_id, "target_topic": "architecture",
          "touches": touches}
    write_page(vault / "learnings" / "notes" / "2026-01" / f"{entry_id}.md", fm, body)


def test_backfill_creates_stub_that_resolves_dangling_concept_edge(atelier_env: Dict):
    vault = atelier_env["gorae"]
    # Two learnings share concept 'dependency-direction'; no entity page exists.
    _accepted(vault, "a", ["dependency-direction"])
    _accepted(vault, "b", ["dependency-direction", "layering"])
    api.reindex(space="gorae", full=True)

    conn = db.connect()
    try:
        # Precondition: the concept edges exist but DANGLE (to_page_id NULL).
        dangling = conn.execute(
            "SELECT COUNT(*) n FROM links WHERE link_type='concept' AND to_page_id IS NULL"
        ).fetchone()["n"]
        assert dangling >= 2, "concept edges should be unresolved before backfill"

        stats = eb.backfill(conn, vault=vault, created="2026-06-12")
    finally:
        conn.close()

    assert stats["created"] >= 2          # dependency-direction + layering
    assert (vault / "graph" / "entities" / "dependency-direction.md").exists()

    # Reindex picks up the new stubs → the concept edges now RESOLVE.
    api.reindex(space="gorae", full=True)
    conn = db.connect()
    try:
        still_dangling = conn.execute(
            "SELECT COUNT(*) n FROM links WHERE link_type='concept' AND to_page_id IS NULL"
        ).fetchone()["n"]
        # the dependency-direction + layering edges now bind
        resolved = conn.execute(
            "SELECT COUNT(*) n FROM links l JOIN pages p ON p.id=l.to_page_id "
            "WHERE l.link_type='concept'").fetchone()["n"]
    finally:
        conn.close()
    assert resolved >= 3, "concept edges should resolve to the new entity stubs"
    assert still_dangling == 0, "no concept edge should remain dangling"


def test_backfill_is_idempotent(atelier_env: Dict):
    vault = atelier_env["gorae"]
    _accepted(vault, "a", ["dependency-direction"])
    api.reindex(space="gorae", full=True)
    conn = db.connect()
    try:
        first = eb.backfill(conn, vault=vault, created="2026-06-12")
        second = eb.backfill(conn, vault=vault, created="2026-06-99")  # diff date
    finally:
        conn.close()
    assert first["created"] >= 1
    assert second["created"] == 0, "second run must create nothing (create-if-missing)"
    assert second["skipped"] >= 1


def test_backfill_skips_concepts_an_existing_entity_already_covers(atelier_env: Dict):
    vault = atelier_env["gorae"]
    # An entity already covers 'dependency-direction' via an alias.
    write_page(
        vault / "graph" / "entities" / "dep-dir.md",
        {"title": "Dependency Direction", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 1, "created": "2026-05-01",
         "updated": "2026-05-01", "provenance": "knowledge", "sensitivity": "public",
         "aliases": ["dependency-direction"]},
        "# Dependency Direction\n\nthe rule.\n",
    )
    _accepted(vault, "a", ["dependency-direction"])
    api.reindex(space="gorae", full=True)
    conn = db.connect()
    try:
        plans = eb.plan_stubs(conn)
    finally:
        conn.close()
    slugs = {p.slug for p in plans}
    assert "dependency-direction" not in slugs, \
        "an alias-covered concept must not get a duplicate stub"
