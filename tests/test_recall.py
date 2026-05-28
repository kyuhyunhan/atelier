"""PR-28: signal-detector recall."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import recall as _rc
from runtime.service.learnings import review as _rev


def _accept(observation: str, why: str, rule: str,
            project: str, topic: str = "general") -> str:
    cap = _cap.capture(
        observation=observation, why=why, rule=rule,
        working_dir=f"/Users/me/workspaces/{project}",
        session_id=project, hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem


# ── basic retrieval (FS fallback path; FTS may not have entries) ──────────


def test_recall_returns_matching_principle(atelier_env: Dict) -> None:
    _pr.add(title="prefer real db",
             rule="integration tests must hit a real database, not mocks.",
             why="mocked tests diverge from prod schema.",
             priority="always-inject",
             slug="prefer-real-db")
    out = _rc.recall(query="mocked database tests", top_k=5)
    assert out["count"] >= 1
    slugs = {it["slug"] for it in out["items"]}
    assert "prefer-real-db" in slugs


def test_recall_returns_matching_accepted_learning(atelier_env: Dict) -> None:
    _accept("react children re-render twice with batching",
             "useTransition needs keys", "stabilize children keys",
             project="bht", topic="rendering")
    out = _rc.recall(query="react children re-render", top_k=5)
    assert out["count"] >= 1
    items = out["items"]
    assert any("react" in (it["snippet"] + it["title"]).lower() for it in items)


# ── project boost ─────────────────────────────────────────────────────────


def test_recall_boosts_current_project(atelier_env: Dict) -> None:
    _accept("foo render flicker", "render bug", "use memo",
             project="lexio", topic="rendering")
    _accept("foo render flicker", "render bug elsewhere", "use memo also",
             project="bht", topic="rendering")
    out = _rc.recall(query="render flicker", project="lexio", top_k=2)
    # Both rendering hits match; lexio (current project) should come first.
    assert out["count"] >= 1
    assert out["items"][0]["project"] == "lexio"


# ── markdown rendering ────────────────────────────────────────────────────


def test_recall_renders_markdown_block(atelier_env: Dict) -> None:
    _pr.add(title="t", rule="x", why="y", priority="always-inject",
             slug="t1")
    out = _rc.recall(query="x", top_k=1, max_chars=500)
    md = out["markdown"]
    assert md.startswith("## atelier — relevant memory")
    assert "[principle]" in md


def test_recall_empty_query_returns_no_items(atelier_env: Dict) -> None:
    _pr.add(title="t", rule="x", why="y", priority="always-inject",
             slug="t1")
    out = _rc.recall(query="", top_k=5)
    assert out["count"] == 0
    assert out["markdown"] == ""


# ── MCP dispatch ──────────────────────────────────────────────────────────


def test_mcp_dispatch_recall(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _accept("react flicker", "x", "y", project="lexio", topic="rendering")

    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_recall",
            query="react flicker",
            project="lexio",
            top_k=3,
        )
    out = asyncio.run(go())
    assert out["count"] >= 1


def test_recall_falls_back_to_working_dir_for_project(atelier_env: Dict) -> None:
    from runtime.service import auth, tools as _tools
    _accept("react flicker", "x", "y", project="lexio", topic="rendering")
    sess = auth.Session(
        agent_kind="claude-code", transport="mcp-http",
        working_dir="/Users/me/workspaces/lexio",
        caller="test", claims=frozenset(),
    )
    tok = _tools.set_session(sess)
    try:
        async def go() -> Dict:
            return await _tools.invoke("atelier_recall", query="react")
        out = asyncio.run(go())
    finally:
        _tools._current.reset(tok)
    assert out["project"] == "lexio"
