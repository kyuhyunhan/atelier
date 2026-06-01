"""PR-20: review / accept / archive / retract for learnings."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import review as _rev


# ── helpers ────────────────────────────────────────────────────────────────


def _make_good_candidate(working_dir: str = "/Users/me/workspaces/lexio") -> Dict:
    """A candidate that satisfies every auto-evaluable must check."""
    return _cap.capture(
        observation="search returns nothing for tilde queries",
        why="fts5 ignores tilde tokens; need fallback path",
        rule="enable fallback for punctuation in queries",
        working_dir=working_dir,
        session_id="abc",
        hook="Stop",
    )


def _make_thin_candidate() -> Dict:
    """A candidate missing 'why', too thin for must_pass. require_why=False
    bypasses the capture-time substance gate so the candidate still exists
    to exercise the downstream review/archive machinery (it will still
    fail must-criteria at promotion time)."""
    return _cap.capture(observation="something", hook="manual",
                        require_why=False)


# ── review_pending ─────────────────────────────────────────────────────────


def test_review_pending_returns_self_check(atelier_env: Dict) -> None:
    good = _make_good_candidate()
    thin = _make_thin_candidate()

    out = _rev.review_pending(limit=10)
    assert out["count"] == 2
    by_id = {item["entry_id"]: item for item in out["items"]}
    assert by_id[good["entry_id"]]["must_pass"] is True
    assert by_id[thin["entry_id"]]["must_pass"] is False


def test_review_pending_filters_by_project(atelier_env: Dict) -> None:
    _make_good_candidate(working_dir="/Users/me/workspaces/lexio")
    _make_good_candidate(working_dir="/Users/me/workspaces/bht")
    out = _rev.review_pending(limit=10, project="bht")
    assert out["count"] == 1
    assert out["items"][0]["project_hint"] == "bht"


# ── accept ─────────────────────────────────────────────────────────────────


def test_accept_promotes_to_by_topic_and_by_project(atelier_env: Dict) -> None:
    good = _make_good_candidate()
    result = _rev.accept(candidate_slug=good["entry_id"],
                         target_topic="search-fallback",
                         target_project="lexio")
    accepted = Path(result["path"])
    assert accepted.exists()
    assert "accepted/by-topic/search-fallback/" in str(accepted)
    by_proj = Path(result["by_project_path"])
    assert by_proj.exists()
    assert "accepted/by-project/lexio/" in str(by_proj)
    # Source candidate must be gone (single source of truth move).
    assert not Path(good["path"]).exists()


def test_accept_refuses_when_must_fails(atelier_env: Dict) -> None:
    thin = _make_thin_candidate()
    with pytest.raises(PermissionError):
        _rev.accept(candidate_slug=thin["entry_id"],
                    target_topic="misc")


def test_accept_writes_log_entry(atelier_env: Dict) -> None:
    good = _make_good_candidate()
    _rev.accept(candidate_slug=good["entry_id"],
                target_topic="search-fallback",
                target_project="lexio")
    log = (atelier_env["gorae"] / "learnings" / "log.md").read_text()
    assert "accept" in log
    assert "search-fallback" in log


# ── archive ────────────────────────────────────────────────────────────────


def test_archive_moves_to_archived(atelier_env: Dict) -> None:
    thin = _make_thin_candidate()
    result = _rev.archive(candidate_slug=thin["entry_id"],
                          reason="pure-meta-comment")
    moved = Path(result["path"])
    assert moved.exists()
    assert "archived/" in str(moved)
    assert not Path(thin["path"]).exists()


# ── retract ────────────────────────────────────────────────────────────────


def test_retract_from_accepted_removes_mirrors(atelier_env: Dict) -> None:
    good = _make_good_candidate()
    accepted = _rev.accept(candidate_slug=good["entry_id"],
                           target_topic="search-fallback",
                           target_project="lexio")
    by_proj = Path(accepted["by_project_path"])
    assert by_proj.exists()
    _rev.retract(slug=Path(accepted["path"]).stem,
                 reason="user-said-no")
    assert not Path(accepted["path"]).exists()
    assert not by_proj.exists()


def test_retract_from_candidate(atelier_env: Dict) -> None:
    thin = _make_thin_candidate()
    out = _rev.retract(slug=thin["entry_id"], reason="too-thin")
    assert "archived/" in out["path"]
    assert out["from"] == "candidate"


# ── MCP dispatch parity ────────────────────────────────────────────────────


def test_mcp_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    expected = {
        "atelier_learning_review_pending",
        "atelier_learning_accept",
        "atelier_learning_archive",
        "atelier_learning_retract",
    }
    assert expected <= names


def test_mcp_dispatch_review_pending(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _make_good_candidate()

    async def go() -> Dict:
        return await _tools.invoke("atelier_learning_review_pending", limit=5)

    out = asyncio.run(go())
    assert out["count"] == 1


# ── override_must (PR-38) ────────────────────────────────────────────────────


def test_override_must_accepts_despite_heuristic_miss(atelier_env: Dict) -> None:
    """A reviewed candidate with free-form why (no '## Why this matters'
    section) fails has_why heuristically; override_must promotes it."""
    thin = _make_thin_candidate()
    # without override → blocked
    with pytest.raises(PermissionError):
        _rev.accept(candidate_slug=thin["entry_id"], target_topic="misc")
    # with override → accepted, and the override is recorded for audit
    out = _rev.accept(candidate_slug=thin["entry_id"], target_topic="misc",
                      target_project="lexio", override_must=True)
    from runtime.index.parse import split_frontmatter
    fm, _ = split_frontmatter(Path(out["path"]).read_text())
    assert fm["status"] == "accepted"
    assert "override_must" in fm["ac_results"]


def test_override_must_cannot_bypass_forbidden(atelier_env: Dict) -> None:
    """forbidden criteria (e.g. pii_leak) are NEVER overridable."""
    cap = _cap.capture(
        observation="config note",
        why="contact admin@example.com with the AKIAIOSFODNN7EXAMPLE key",
        working_dir="/Users/me/workspaces/lexio", hook="manual",
    )
    with pytest.raises(PermissionError) as ei:
        _rev.accept(candidate_slug=cap["entry_id"], target_topic="misc",
                    override_must=True)
    assert ei.value.args[0]["forbidden_triggered"]
