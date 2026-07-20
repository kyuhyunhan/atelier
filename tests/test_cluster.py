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


def _proactive(vault: Path, eid: str) -> None:
    """Write a minimal v7 claim at surfacing:proactive — the dream cadence now
    counts the proactive pool (dream's input, any domain), not accepted learnings."""
    import yaml
    from runtime.structure import resolver as _structure
    fm = {"entry_id": eid, "schema_version": 7, "kind": "claim",
          "statement": f"claim {eid}", "surfacing": "proactive",
          "domain": "knowledge", "sensitivity": "public", "content_hash": "h",
          "created_at": "2026-01-01T00:00:00Z", "generated_by": "atomize",
          "is_about": [], "derived_from": ["s"]}
    d = vault / _structure.atomic_claim_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{eid}.md").write_text(
        "---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n\nx\n",
        encoding="utf-8")


def test_dream_status_counts_since_baseline(atelier_env: Dict) -> None:
    _proactive(atelier_env["gorae"], "p1")
    st = _cl.dream_status()
    assert st["proactive_since_last_dream"] >= 1
    assert st["last_dream_at"] is None


def test_mark_dream_complete_resets_baseline(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _proactive(vault, "p1")
    _cl.mark_dream_complete(when="2026-05-28T20:00:00+09:00")
    st = _cl.dream_status()
    assert st["last_dream_at"] == "2026-05-28T20:00:00+09:00"
    assert st["proactive_since_last_dream"] == 0
    # A new proactive claim after the dream shows up as +1.
    _proactive(vault, "p2")
    st2 = _cl.dream_status()
    assert st2["proactive_since_last_dream"] == 1


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
