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
        working_dir=str(cwd),
        hook="manual",
    )
    fm = _read_fm(Path(result["path"]))
    assert fm["project_hint"] == "atelier-self"


def test_capture_permissive_on_missing_fields(atelier_env: Dict) -> None:
    """Empty why must still produce a candidate (aggressive capture)."""
    result = _cap.capture(observation="something happened", hook="Stop")
    path = Path(result["path"])
    assert path.exists()
    # The body keeps the empty Why section so the reviewer sees it.
    text = path.read_text(encoding="utf-8")
    assert "## Why this matters" in text


def test_capture_collision_avoided(atelier_env: Dict) -> None:
    """Two captures within the same minute should not collide."""
    a = _cap.capture(observation="alpha", hook="Stop")
    b = _cap.capture(observation="alpha", hook="Stop")
    assert a["path"] != b["path"]


def test_mcp_tool_dispatch_invokes_capture(atelier_env: Dict) -> None:
    """The MCP tool registration must wire through tools.invoke()."""
    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_learning_capture",
            observation="invoked via MCP",
            hook="manual",
        )
    out = asyncio.run(go())
    assert Path(out["path"]).exists()


def test_capture_refuses_when_vault_missing(atelier_env: Dict,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Vault root must exist; otherwise we surface a real error."""
    import shutil as _sh
    _sh.rmtree(atelier_env["gorae"])
    with pytest.raises(FileNotFoundError):
        _cap.capture(observation="foo", hook="Stop")
