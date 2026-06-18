"""P0 — characterization (golden) tests for the flat-facet learnings migration.

These pin the CURRENT observable behavior of the three context-injection readers
— recall, session_bootstrap, surfacing — against ONE shared fixture in the
production single-vault shape. They are the regression oracle for the migration:
the same assertions must pass UNCHANGED through P1 (schema), P2 (facet index),
P3 (facet resolver), and P4 (on-disk flatten). If any of these flips, a reader
started surfacing a different set of learnings — exactly the silent-omission
risk the migration must not introduce.

They assert on RESULTS (which learnings surface, relative rank, visibility),
never on storage layout — so they survive the by-topic → notes/<YYYY-MM>/ move
by construction.
"""
from __future__ import annotations

from typing import Dict, Optional

from runtime.service import api
from runtime.service.learnings import bootstrap as _bs
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import recall as _rc
from runtime.service.learnings import surfacing as _sf
from tests.conftest import write_page


_BASE = {
    "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "feedback",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
}


def _accepted(vault, *, entry_id: str, topic: str, body: str,
              project: Optional[str] = None, touches=None) -> None:
    """Seed an accepted learning in the flat notes/ store (RFC 0001)."""
    fm = {**_BASE, "entry_id": entry_id, "target_topic": topic}
    if project:
        fm["target_project"] = project
    if touches:
        fm["touches"] = touches
    write_page(vault / "raw" / "learning" / "notes" / "2026-01" /
               f"{entry_id}.md", fm, body)


def _seed(vault) -> None:
    """A representative multi-project / multi-topic corpus.

    L1/L3 are a project-boost pair (same topic, different project). L2/L4 share a
    `touches` concept across projects (folder-free cross-cut). Bodies carry the
    probe words so FTS — the live retrieval path — can find each by its concept.
    """
    _accepted(vault, entry_id="L1", topic="rendering", project="lexio",
              touches=["render-batching"],
              body="## Observation\n\nreact children re-render twice with "
                   "batching; stabilize keys to avoid render flicker\n")
    _accepted(vault, entry_id="L2", topic="architecture", project="lexio",
              touches=["dependency-direction"],
              body="## Observation\n\ndepend on protocols not implementations; "
                   "dependency direction points inward\n")
    _accepted(vault, entry_id="L3", topic="rendering", project="bht",
              body="## Observation\n\nreact render flicker on mount elsewhere; "
                   "batching subtlety\n")
    _accepted(vault, entry_id="L4", topic="architecture", project="app",
              touches=["dependency-direction"],
              body="## Observation\n\nlayering boundaries enforce protocols; "
                   "dependency direction inward\n")
    _pr.add(title="prefer real db",
            rule="integration tests must hit a real database, not mocks.",
            why="mocked tests diverge from prod schema.",
            priority="always-inject", slug="prefer-real-db")
    api.reindex(full=True)


# ── recall ──────────────────────────────────────────────────────────────────


def test_recall_project_boost_orders_current_project_first(vault_env: Dict) -> None:
    """L1 (lexio) and L3 (bht) both match 'render flicker'; the current project
    (lexio) must rank first. GOLDEN: project boost ordering."""
    _seed(vault_env["vault"])
    out = _rc.recall(query="render flicker", project="lexio", top_k=5)
    assert out["count"] >= 2
    assert out["items"][0]["project"] == "lexio"


def test_recall_finds_each_learning_by_its_concept(vault_env: Dict) -> None:
    """GOLDEN: every seeded learning is reachable by a query carrying its concept."""
    _seed(vault_env["vault"])
    probes = {
        "react batching render": "L1",
        "dependency direction protocols": None,   # L2 or L4 (both legit)
        "layering boundaries": "L4",
    }
    for query, expect in probes.items():
        out = _rc.recall(query=query, top_k=5)
        assert out["count"] >= 1, f"{query!r} returned nothing"
        if expect:
            slugs = " ".join(it["slug"] for it in out["items"])
            assert expect in slugs, f"{query!r} did not surface {expect}"


def test_recall_concept_overlap_boost_is_active(vault_env: Dict) -> None:
    """GOLDEN: a `touches` concept matching the query boosts rank even on weak
    body overlap (the concept-index payoff)."""
    base = _rc._boost({"score": 0.0, "fm": {}, "page_type": "learning_accepted"},
                      None, frozenset({"dependency"}))
    boosted = _rc._boost(
        {"score": 0.0, "fm": {"touches": ["dependency-direction"],
                              "target_topic": "architecture"},
         "page_type": "learning_accepted"},
        None, frozenset({"dependency"}))
    assert boosted > base   # P3: positive RRF-scale boost, descending sort


# ── session bootstrap ─────────────────────────────────────────────────────────


def test_bootstrap_injects_project_learnings_and_isolates(vault_env: Dict) -> None:
    """GOLDEN: lexio's bootstrap carries its own learnings + always-inject
    principle, and does NOT leak bht's project-only learning."""
    _seed(vault_env["vault"])
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    md = out["markdown"]
    assert "learnings for project `lexio`" in md
    assert "prefer real db" in md                       # always-inject principle
    assert out["principles_count"] == 1


def test_bootstrap_cross_cuts_on_shared_touches(vault_env: Dict) -> None:
    """GOLDEN: app's bootstrap surfaces lexio's L2 via the shared
    `dependency-direction` concept — connection by idea, not folder. This is the
    behavior the flatten must preserve (it is already folder-free)."""
    _seed(vault_env["vault"])
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/app")
    assert "related by concept" in out["markdown"]


# ── surfacing audit ────────────────────────────────────────────────────────────


def test_surfacing_all_seeded_learnings_visible(vault_env: Dict) -> None:
    """GOLDEN: each seeded learning is findable by its own concept (none dark).
    The migration's surfacing diff (before P4 vs after P7) must keep this set —
    an empty `newly_dark` is the acceptance gate."""
    _seed(vault_env["vault"])
    snap = _sf.snapshot()
    assert set(snap) == {"L1", "L2", "L3", "L4"}
    assert all(snap[e]["visible"] for e in snap), \
        {e: snap[e]["visible"] for e in snap}
    report = _sf.audit()
    assert report["dark"] == []
