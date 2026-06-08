"""P2 — facet index population at reindex (RFC 0001 §4).

The resolver (P3) filters on these rows instead of scanning frontmatter blobs.
Population must be deterministic (same markdown → same rows) and idempotent
(re-running reindex never duplicates).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from runtime.service import api
from runtime.util import db as _db
from tests.conftest import write_page


_BASE = {
    "schema_version": 5, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "project",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
}


def _seed_one(vault) -> None:
    fm = {**_BASE, "entry_id": "F1", "target_project": "lexio",
          "aspect": ["client", "cross-cutting"], "target_topic": "rendering",
          "touches": ["render-batching"]}
    write_page(vault / "learnings" / "accepted" / "by-topic" / "rendering" /
               "F1.md", fm, "## Observation\n\nbatching body words\n")


def _facets() -> List[Tuple[str, str]]:
    conn = _db.connect()
    try:
        return sorted((r["kind"], r["value"]) for r in
                      conn.execute("SELECT kind, value FROM learning_facets"))
    finally:
        conn.close()


def test_facets_populated_for_each_kind(vault_env: Dict) -> None:
    _seed_one(vault_env["vault"])
    api.reindex(full=True)
    rows = _facets()
    assert ("project", "lexio") in rows
    assert ("aspect", "client") in rows            # many-valued → multiple rows
    assert ("aspect", "cross-cutting") in rows
    assert ("topic", "rendering") in rows
    assert ("touches", "render-batching") in rows


def test_many_valued_aspect_yields_n_rows(vault_env: Dict) -> None:
    _seed_one(vault_env["vault"])
    api.reindex(full=True)
    aspects = sorted(v for k, v in _facets() if k == "aspect")
    assert aspects == ["client", "cross-cutting"]


def test_reindex_is_idempotent_no_duplicate_rows(vault_env: Dict) -> None:
    _seed_one(vault_env["vault"])
    api.reindex(full=True)
    first = _facets()
    api.reindex(full=True)                          # clear-and-repopulate per page
    second = _facets()
    assert first == second
    # no duplicates: every (kind, value) is unique for this single learning.
    assert len(second) == len(set(second))


def test_topicless_learning_has_no_topic_facet(vault_env: Dict) -> None:
    """A v5 learning with no target_topic contributes project/aspect facets but
    no 'topic' row — the demotion is honored end-to-end."""
    vault = vault_env["vault"]
    fm = {**_BASE, "entry_id": "F2", "target_project": "pmi",
          "aspect": ["persistence"]}      # no target_topic
    write_page(vault / "learnings" / "accepted" / "by-topic" / "misc" /
               "F2.md", fm, "## Observation\n\nrepository owns unit of work\n")
    api.reindex(full=True)
    rows = _facets()
    assert ("project", "pmi") in rows
    assert ("aspect", "persistence") in rows
    assert not any(k == "topic" for k, _ in rows)
