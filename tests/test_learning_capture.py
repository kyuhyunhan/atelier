"""PR-19: learning_capture handler + atelier-mcp-call CLI."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict

import pytest

from runtime.service import tools as _tools
from runtime.service.learnings import capture as _cap


def _read_fm(path: Path) -> dict:
    from runtime.index.parse import split_frontmatter
    fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def test_capture_writes_under_candidates(atelier_env: Dict) -> None:
    result = _cap.capture(
        observation="search returns nothing for tilde queries",
        why="fts5 ignores tilde tokens; need fallback",
        rule="enable fallback for punctuation in queries",
        working_dir="/Users/me/workspaces/lexio",
        session_id="abc",
        hook="Stop",
    )
    path = Path(result["path"])
    assert path.exists()
    assert "learnings/candidates/" in str(path)
    fm = _read_fm(path)
    assert fm["status"] == "candidate"
    assert fm["ac_status"] == "pending"
    assert fm["agent_kind"] == "claude-code"
    assert fm["hook"] == "Stop"
    assert fm["project_hint"] == "lexio"
    assert fm["entry_id"] == result["entry_id"]


def test_capture_inside_vault_tags_atelier_self(atelier_env: Dict) -> None:
    """working_dir under the vault root → project_hint = atelier-self."""
    cwd = atelier_env["gorae"] / "wiki"
    cwd.mkdir(exist_ok=True)
    result = _cap.capture(
        observation="dogfooding atelier itself",
        why="confirms the engine works on its own vault",
        working_dir=str(cwd),
        hook="manual",
    )
    fm = _read_fm(Path(result["path"]))
    assert fm["project_hint"] == "atelier-self"


# ── substance gate (C) ──────────────────────────────────────────────────────


def test_capture_flags_empty_why_but_writes(atelier_env: Dict) -> None:
    """RFC 0004 phase 2: an observation with no why is NO LONGER rejected.
    It is written, flagged why_status=missing, and the result carries a soft
    why_missing nudge (require_why defaults True)."""
    result = _cap.capture(observation="something happened", hook="Stop")
    assert "skipped" not in result
    path = Path(result["path"])
    assert path.exists()
    assert result["why_status"] == "missing"
    assert result["why_missing"] is True
    assert _read_fm(path)["why_status"] == "missing"


def test_capture_require_why_false_suppresses_nudge(atelier_env: Dict) -> None:
    """With require_why=False (session-end hook / absorbed memory), an empty
    why still writes + flags missing, but emits no why_missing nudge."""
    result = _cap.capture(observation="hook-derived observation",
                          require_why=False, hook="SessionEnd")
    assert result["why_status"] == "missing"
    assert "why_missing" not in result
    assert Path(result["path"]).exists()


def test_capture_present_why_sets_status(atelier_env: Dict) -> None:
    result = _cap.capture(observation="x happens under y",
                          why="because z, which prevents w", hook="manual")
    assert result["why_status"] == "present"
    assert "why_missing" not in result
    assert _read_fm(Path(result["path"]))["why_status"] == "present"


def test_capture_rejects_stub_observation(atelier_env: Dict) -> None:
    """A bare hook stub (no real observation, no why) → no-substance."""
    result = _cap.capture(
        observation="(hook=Stop) session_id=abc-123",
        hook="Stop",
    )
    assert result["skipped"] is True
    assert result["reason"] == "no-substance"


def test_capture_accepts_when_why_present(atelier_env: Dict) -> None:
    result = _cap.capture(
        observation="search returns nothing for tilde queries",
        why="fts5 ignores tilde tokens; users see a silent empty result",
        hook="manual",
    )
    assert Path(result["path"]).exists()


def test_capture_require_why_false_writes(atelier_env: Dict) -> None:
    """Sources with free-form rationale (e.g. absorbed Claude memory) write
    without a why and without a nudge."""
    result = _cap.capture(
        observation="absorbed memory with prose rationale inline",
        require_why=False, hook="manual",
    )
    assert Path(result["path"]).exists()
    assert "why_missing" not in result


def test_capture_collision_avoided(atelier_env: Dict) -> None:
    """Two captures within the same minute should not collide."""
    a = _cap.capture(observation="alpha", why="because a", hook="Stop")
    b = _cap.capture(observation="alpha", why="because a", hook="Stop")
    assert a["path"] != b["path"]


def test_mcp_tool_dispatch_invokes_capture(atelier_env: Dict) -> None:
    """The MCP tool registration must wire through tools.invoke()."""
    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_learning_capture",
            observation="invoked via MCP",
            why="verifies the tool wiring end to end",
            hook="manual",
        )
    out = asyncio.run(go())
    assert Path(out["path"]).exists()


def test_mcp_tool_dispatch_flags_empty_why(atelier_env: Dict) -> None:
    """The tool layer writes an empty-why capture and flags it missing."""
    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_learning_capture",
            observation="hook fired but no judgement",
            hook="Stop",
        )
    out = asyncio.run(go())
    assert "skipped" not in out
    assert out["why_status"] == "missing"
    assert Path(out["path"]).exists()


def test_capture_refuses_when_vault_missing(atelier_env: Dict,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Vault root must exist; otherwise we surface a real error. (Provide
    a why so the substance gate passes and we reach the vault check.)"""
    import shutil as _sh
    _sh.rmtree(atelier_env["gorae"])
    with pytest.raises(FileNotFoundError):
        _cap.capture(observation="foo", why="bar", hook="Stop")
