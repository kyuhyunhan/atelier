"""PR-33: dream orchestration (plan / complete handshake)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List

import pytest

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import dream as _dr
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import review as _rev


def _accept(seed: str, project: str, topic: str = "testing") -> str:
    cap = _cap.capture(
        observation=f"database mocks diverge from production database {seed}",
        why="database mocks hide schema drift from the production database",
        rule="integration tests must use a real database not database mocks",
        working_dir=f"/Users/me/workspaces/{project}",
        session_id=seed, hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return cap["entry_id"]


def _seed_cross_project_cluster() -> List[str]:
    e1 = _accept("a", "lexio")
    e2 = _accept("b", "bht")
    return [e1, e2]


# ── plan ────────────────────────────────────────────────────────────────────


def test_plan_returns_cluster_with_previews_and_call(atelier_env: Dict) -> None:
    _seed_cross_project_cluster()
    out = _dr.plan()
    assert out["candidate_count"] >= 1
    c = out["clusters"][0]
    assert set(c["projects"]) == {"lexio", "bht"}
    assert len(c["members"]) == 2
    # Each member preview has a title + rule line.
    assert all(m["title"] for m in c["members"])
    # Ready-to-fill synthesize call carries the stable identifiers.
    call = c["synthesize_call"]["args"]
    assert call["status"] == "proposed"
    assert call["cluster_key"] == c["cluster_key"]
    assert len(call["source_entry_ids"]) == 2
    assert "cadence" in out and "instructions" in out


def test_plan_filters_already_covered(atelier_env: Dict) -> None:
    eids = _seed_cross_project_cluster()
    # Synthesize a proposal covering this cluster.
    out1 = _dr.plan()
    args = out1["clusters"][0]["synthesize_call"]["args"]
    _pr.synthesize(
        source_slugs=args["source_slugs"],
        source_entry_ids=args["source_entry_ids"],
        cluster_key=args["cluster_key"],
        title="covered", slug="covered",
    )
    # Re-plan → the covered cluster is filtered out.
    out2 = _dr.plan()
    assert out2["candidate_count"] == 0
    assert out2["skipped_already_covered"] >= 1


# ── complete ────────────────────────────────────────────────────────────────


def test_complete_advances_cadence(atelier_env: Dict) -> None:
    _seed_cross_project_cluster()
    before = _cl.dream_status()
    assert before["last_dream_at"] is None
    out = _dr.complete(when="2026-05-28T21:00:00+09:00")
    assert out["last_dream_at"] == "2026-05-28T21:00:00+09:00"
    after = _cl.dream_status()
    assert after["last_dream_at"] == "2026-05-28T21:00:00+09:00"
    assert after["accepted_since_last_dream"] == 0


def test_complete_reports_pending_proposals(atelier_env: Dict) -> None:
    _seed_cross_project_cluster()
    _pr.add(title="a draft", rule="r", why="w", status="proposed", slug="d")
    out = _dr.complete(when="2026-05-28T21:00:00+09:00")
    assert out["proposed_awaiting_review"] == 1


# ── full handshake ──────────────────────────────────────────────────────────


def test_full_dream_handshake(atelier_env: Dict) -> None:
    """plan → synthesize each cluster → complete → nudge clears."""
    _seed_cross_project_cluster()
    plan = _dr.plan()
    for c in plan["clusters"]:
        args = c["synthesize_call"]["args"]
        _pr.synthesize(
            source_slugs=args["source_slugs"],
            source_entry_ids=args["source_entry_ids"],
            cluster_key=args["cluster_key"],
            title="real db over mocks",
            rule="integration tests use a real database",
            why="mocks drift from prod",
        )
    _dr.complete(when="2026-05-28T21:00:00+09:00")
    # A re-plan now finds nothing (all covered).
    replan = _dr.plan()
    assert replan["candidate_count"] == 0
    # And a proposed draft awaits review.
    assert _pr.review_proposed()["count"] >= 1


# ── MCP dispatch ────────────────────────────────────────────────────────────


def test_mcp_dream_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    assert {"atelier_dream_plan", "atelier_dream_complete",
            "atelier_dream_status"} <= names


def test_mcp_dispatch_dream_plan(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _seed_cross_project_cluster()

    async def go() -> Dict:
        return await _tools.invoke("atelier_dream_plan")

    out = asyncio.run(go())
    assert out["candidate_count"] >= 1
