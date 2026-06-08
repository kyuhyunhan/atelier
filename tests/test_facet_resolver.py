"""P3 — the resolver filters on the indexed learning_facets table (RFC 0001).

search() hard-filters project/topic/aspect via EXISTS over learning_facets
instead of scanning frontmatter; recall() gains optional aspect/topic scoping
while keeping project as a boost.
"""
from __future__ import annotations

from typing import Dict

from runtime.service import api
from runtime.service.learnings import recall as _rc
from runtime.service.learnings import search as _se
from tests.conftest import write_page


_BASE = {
    "schema_version": 5, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "project",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
}


def _seed(vault) -> None:
    write_page(vault / "learnings" / "notes" / "2026-01" / "C.md",
               {**_BASE, "entry_id": "C", "target_project": "lexio",
                "aspect": ["client"]},
               "## Observation\n\noverlay hotkey clipboard pipeline\n")
    write_page(vault / "learnings" / "notes" / "2026-01" / "S.md",
               {**_BASE, "entry_id": "S", "target_project": "lexio",
                "aspect": ["server"]},
               "## Observation\n\nlambda dynamodb api gateway pipeline\n")
    write_page(vault / "learnings" / "notes" / "2026-01" / "B.md",
               {**_BASE, "entry_id": "B", "target_project": "bht",
                "aspect": ["client"]},
               "## Observation\n\nreact rendering pipeline\n")
    api.reindex(full=True)


def test_search_aspect_filter_scopes_via_facet_index(vault_env: Dict) -> None:
    _seed(vault_env["vault"])
    out = _se.search(status="accepted", aspect="server")
    ids = {h["entry_id"] for h in out["items"]}
    assert ids == {"S"}                       # only the server-aspect learning


def test_search_project_and_aspect_compose(vault_env: Dict) -> None:
    _seed(vault_env["vault"])
    out = _se.search(status="accepted", project="lexio", aspect="client")
    ids = {h["entry_id"] for h in out["items"]}
    assert ids == {"C"}                        # lexio ∧ client → C only (not B, not S)


def test_search_project_filter_uses_facet_table(vault_env: Dict) -> None:
    _seed(vault_env["vault"])
    out = _se.search(status="accepted", project="bht")
    ids = {h["entry_id"] for h in out["items"]}
    assert ids == {"B"}


def test_recall_aspect_scope_hard_filters(vault_env: Dict) -> None:
    """recall(aspect=…) hard-scopes; without it, project stays a boost (both
    projects' client learnings remain eligible)."""
    _seed(vault_env["vault"])
    scoped = _rc.recall(query="pipeline", aspect="server", top_k=5)
    stems = {it["slug"].rsplit("/", 1)[-1].removesuffix(".md")
             for it in scoped["items"]}
    assert stems == {"S"}                       # all three bodies say 'pipeline',
    #                                             but the server-aspect scope wins.

    unscoped = _rc.recall(query="pipeline", top_k=5)
    assert unscoped["count"] >= 2              # no facet scope → broader set
