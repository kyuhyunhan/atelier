"""PR-24.5: principles/ tier — cross-project developer ethos."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest
import yaml

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import review as _rev


def _accept(observation: str, why: str, rule: str,
            project: str, topic: str = "general") -> str:
    cap = _cap.capture(
        observation=observation, why=why, rule=rule,
        working_dir=f"/Users/me/workspaces/{project}",
        session_id="s", hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem


def _read_fm(path: Path) -> Dict:
    from runtime.index.parse import split_frontmatter
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


# ── add ────────────────────────────────────────────────────────────────────


def test_add_writes_principle(atelier_env: Dict) -> None:
    out = _pr.add(
        title="prefer real db in integration tests",
        rule="integration tests must hit a real database, not mocks.",
        why="mocked tests pass while prod migration fails (lexio 2026-03; bht 2026-04).",
        evidence=["learnings/accepted/by-project/lexio/foo.md"],
        coverage="cross-project",
        priority="always-inject",
    )
    p = Path(out["path"])
    assert p.exists()
    assert "learnings/principles/" in str(p)
    fm = _read_fm(p)
    assert fm["coverage"] == "cross-project"
    assert fm["priority"] == "always-inject"
    assert fm["status"] == "accepted"
    body = p.read_text()
    assert "## Rule" in body and "mocks" in body
    assert "## Evidence" in body


def test_add_refuses_collision(atelier_env: Dict) -> None:
    _pr.add(title="rule one", rule="r", why="w",
            slug="rule-one")
    with pytest.raises(FileExistsError):
        _pr.add(title="another", rule="r", why="w", slug="rule-one")


def test_add_rejects_bad_priority(atelier_env: Dict) -> None:
    with pytest.raises(ValueError, match="priority"):
        _pr.add(title="t", rule="r", why="w", priority="urgent")


# ── synthesize ─────────────────────────────────────────────────────────────


def test_synthesize_from_two_accepted_learnings(atelier_env: Dict) -> None:
    s1 = _accept("lexio mock bug", "mocked db diverged from prod migration",
                  "use real db in IT", project="lexio", topic="db-tests")
    s2 = _accept("bht mock bug", "same issue on bht repo", "real db only",
                  project="bht", topic="db-tests")

    out = _pr.synthesize(
        source_slugs=[s1, s2],
        title="real db over mocks",
        rule="integration tests must use the real db.",
        why="mocked tests silently diverge from prod schema; cost 2 incidents.",
        coverage="cross-project",
        priority="always-inject",
    )
    p = Path(out["path"])
    fm = _read_fm(p)
    assert fm["coverage"] == "cross-project"
    assert fm["priority"] == "always-inject"
    # Two evidence backlinks resolved to vault-relative paths.
    assert len(fm["evidence"]) == 2
    assert all(e.startswith("learnings/notes/") for e in fm["evidence"])
    body = p.read_text()
    for e in fm["evidence"]:
        assert f"[[{e}]]" in body


def test_synthesize_leaves_scaffold_when_rule_blank(atelier_env: Dict) -> None:
    s = _accept("x", "y", "rule x", project="lexio", topic="db-tests")
    out = _pr.synthesize(source_slugs=[s], title="todo principle")
    body = Path(out["path"]).read_text()
    assert "(fill in: the principle in one or two sentences)" in body
    assert out["fields_to_fill"] == ["rule", "why"]


def test_synthesize_refuses_missing_source(atelier_env: Dict) -> None:
    with pytest.raises(FileNotFoundError):
        _pr.synthesize(source_slugs=["does-not-exist"])


# ── list / archive ─────────────────────────────────────────────────────────


def test_list_filters_by_priority(atelier_env: Dict) -> None:
    _pr.add(title="always-1", rule="r", why="w", priority="always-inject")
    _pr.add(title="relevant-1", rule="r", why="w", priority="on-relevant-prompt")
    out = _pr.list_all(priority="always-inject")
    assert len(out) == 1
    assert out[0]["priority"] == "always-inject"


def test_archive_moves_principle(atelier_env: Dict) -> None:
    out = _pr.add(title="stale", rule="r", why="w", slug="stale-one")
    res = _pr.archive(slug="stale-one", reason="outdated")
    assert not Path(out["path"]).exists()
    assert "archived/" in res["path"]
    assert Path(res["path"]).exists()


# ── MCP dispatch ───────────────────────────────────────────────────────────


def test_mcp_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    expected = {
        "atelier_principle_add",
        "atelier_principle_synthesize",
        "atelier_principle_list",
        "atelier_principle_archive",
    }
    assert expected <= names


def test_mcp_dispatch_principle_add(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_principle_add",
            title="mcp-added",
            rule="r",
            why="w",
        )
    out = asyncio.run(go())
    assert Path(out["path"]).exists()
