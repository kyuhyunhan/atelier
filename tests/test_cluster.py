"""PR-29: deterministic learning clustering + dream cadence tracking."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import review as _rev


def _accept(observation: str, why: str, rule: str,
            project: str, topic: str) -> str:
    cap = _cap.capture(
        observation=observation, why=why, rule=rule,
        working_dir=f"/Users/me/workspaces/{project}",
        session_id=f"{project}-{topic}", hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem


# ── clustering ──────────────────────────────────────────────────────────────


def test_cross_project_cluster_detected(atelier_env: Dict) -> None:
    # Two projects, same vocabulary about database mocking → one cluster.
    _accept("integration tests used database mocks and diverged",
            "mocked database schema drifted from production database",
            "integration tests must hit a real database not database mocks",
            project="lexio", topic="testing")
    _accept("database mocks in integration tests caused production failure",
            "database mocks hide schema drift in production database",
            "avoid database mocks; integration tests need real database",
            project="bht", topic="testing")

    out = _cl.cluster(min_shared_terms=3, min_size=2, min_projects=2)
    assert out["cluster_count"] >= 1
    c = out["clusters"][0]
    assert set(c["projects"]) == {"lexio", "bht"}
    assert c["size"] == 2
    assert "database" in c["shared_terms"]


def test_single_project_cluster_excluded(atelier_env: Dict) -> None:
    # Both learnings in the SAME project → fails min_projects=2.
    _accept("database mocks diverge from production database schema badly",
            "database mocks hide drift", "use real database in tests always",
            project="lexio", topic="testing")
    _accept("database mocks again diverge from the production database schema",
            "database mocks still hide drift", "real database required in tests",
            project="lexio", topic="testing2")
    out = _cl.cluster(min_shared_terms=3, min_size=2, min_projects=2)
    assert out["cluster_count"] == 0


def test_unrelated_learnings_not_clustered(atelier_env: Dict) -> None:
    _accept("react children rerender twice with concurrent batching enabled",
            "concurrent batching", "stabilize react children keys carefully",
            project="lexio", topic="rendering")
    _accept("postgres connection pool exhausted under heavy parallel load",
            "connection pool", "raise postgres pool size for parallel workloads",
            project="bht", topic="database")
    out = _cl.cluster(min_shared_terms=3, min_size=2, min_projects=2)
    assert out["cluster_count"] == 0


def test_clustering_is_deterministic(atelier_env: Dict) -> None:
    _accept("database mocks diverge from production database schema here",
            "database mocks drift", "real database required for integration",
            project="lexio", topic="testing")
    _accept("database mocks diverge from production database schema there",
            "database mocks drift again", "real database needed for integration",
            project="bht", topic="testing")
    a = _cl.cluster()
    b = _cl.cluster()
    assert a["clusters"] == b["clusters"]
    # Same members → same stable cluster_key.
    if a["clusters"]:
        assert a["clusters"][0]["cluster_key"] == b["clusters"][0]["cluster_key"]


# ── dream cadence ───────────────────────────────────────────────────────────


def test_dream_status_counts_since_baseline(atelier_env: Dict) -> None:
    _accept("a learning about caching strategy in production systems today",
            "caching", "cache aggressively", project="lexio", topic="perf")
    st = _cl.dream_status()
    assert st["accepted_since_last_dream"] >= 1
    assert st["last_dream_at"] is None


def test_mark_dream_complete_resets_baseline(atelier_env: Dict) -> None:
    _accept("first learning about logging discipline across the services",
            "logging", "log structured", project="lexio", topic="ops")
    _cl.mark_dream_complete(when="2026-05-28T20:00:00+09:00")
    st = _cl.dream_status()
    assert st["last_dream_at"] == "2026-05-28T20:00:00+09:00"
    assert st["accepted_since_last_dream"] == 0
    # A new acceptance after the dream shows up as +1.
    _accept("second learning about retry backoff in distributed callers",
            "retry", "exponential backoff", project="bht", topic="ops")
    st2 = _cl.dream_status()
    assert st2["accepted_since_last_dream"] == 1


# ── MCP dispatch ────────────────────────────────────────────────────────────


def test_mcp_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    assert "atelier_learning_cluster" in names
    assert "atelier_dream_status" in names


def test_mcp_dispatch_cluster(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _accept("database mocks diverge from production database schema now",
            "database mocks drift", "real database required for the tests",
            project="lexio", topic="testing")
    _accept("database mocks diverge from production database schema yet",
            "database mocks drift more", "real database needed in the tests",
            project="bht", topic="testing")

    async def go() -> Dict:
        return await _tools.invoke("atelier_learning_cluster")

    out = asyncio.run(go())
    assert out["cluster_count"] >= 1
