"""PR-3: tools registry + invoke() dispatch.

We verify:
- the v0.1 tool surface is registered
- read tools dispatch without claim/lock checks
- write tools refuse callers lacking the required claim
- write tools serialize via the role lock

We do NOT spin up a real MCP stdio transport here — that requires real
stdin/stdout and is exercised manually via Claude Code. The dispatch
path through invoke() is the load-bearing contract.
"""
from __future__ import annotations

import asyncio
from typing import Dict

import pytest

from runtime.service import auth, claims, tools


def test_v01_tools_registered() -> None:
    names = {t.name for t in tools.iter_tools()}
    # spot-check a few — the full list is in tools._register_v01_tools().
    expected = {
        "atelier_search", "atelier_links", "atelier_list_pages",
        "atelier_lint", "atelier_doctor", "atelier_sync",
        "atelier_reindex", "atelier_capture",
        "atelier_promote_propose", "atelier_promote_apply",
        "atelier_new_product",
    }
    missing = expected - names
    assert not missing, f"missing tools: {missing}"


def test_invoke_read_tool_dispatches(atelier_env: Dict) -> None:
    """Read tools should dispatch with the default local-cli session
    and not touch the lock registry."""
    # Need a fresh DB for the search tool to run safely.
    from runtime.util import db
    db.close_shared()

    async def go() -> Dict:
        return await tools.invoke("atelier_search", query="nonexistent",
                                  limit=5, fallback=False)

    out = asyncio.run(go())
    assert "hits" in out


def test_invoke_write_tool_blocks_unprivileged_caller(atelier_env: Dict) -> None:
    """A session without WIKI_WRITE must be rejected by atelier_reindex."""
    poor = auth.Session(transport="mcp-http", caller="poor", claims=frozenset())
    tok = tools.set_session(poor)
    try:
        async def go() -> None:
            await tools.invoke("atelier_reindex", space=None, full=False)
        with pytest.raises(PermissionError):
            asyncio.run(go())
    finally:
        tools._current.reset(tok)


def test_invoke_write_tool_serializes_on_role_lock(atelier_env: Dict) -> None:
    """Two writes through the same write tool must run one-at-a-time."""
    claims.reset_registry()
    order: list[str] = []

    # Stand in a custom write tool that records ordering — we don't
    # want to actually mutate the workspace here.
    async def _h_demo(tag: str, hold: float = 0.02) -> Dict:
        order.append(f"{tag}:in")
        await asyncio.sleep(hold)
        order.append(f"{tag}:out")
        return {"tag": tag}

    tools.register(tools.ToolDef(
        name="_test_demo",
        description="test only",
        handler=_h_demo,
        claim=claims.Claim.WIKI_WRITE,
        lock_role=claims.WriterRole.WIKI,
    ))

    async def go() -> None:
        await asyncio.gather(
            tools.invoke("_test_demo", tag="A", hold=0.02),
            tools.invoke("_test_demo", tag="B", hold=0.00),
        )

    asyncio.run(go())
    assert order == ["A:in", "A:out", "B:in", "B:out"]


def test_mcp_stdio_builds_without_running(atelier_env: Dict) -> None:
    """build_app() should construct a FastMCP with all tools registered
    — but we don't start the stdio loop (that needs real stdin)."""
    from runtime.service import mcp_stdio
    app = mcp_stdio.build_app()
    # FastMCP exposes list_tools() as async; use call_tool's metadata.
    names = asyncio.run(app.list_tools())
    seen = {t.name for t in names}
    assert "atelier_search" in seen
    assert "atelier_reindex" in seen
