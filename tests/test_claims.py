"""PR-2: SpaceLockRegistry + Session/auth bearer.

The registry must:
- serialize same-role writers (one acquire at a time)
- run different-role writers in parallel

Bearer auth must:
- reject when env var unset
- reject mismatched tokens
- accept matching tokens and populate Session
"""
from __future__ import annotations

import asyncio

import pytest

from runtime.service import auth, claims


# ── SpaceLockRegistry ─────────────────────────────────────────────────────────


def test_same_role_serializes() -> None:
    claims.reset_registry()
    reg = claims.registry()
    order: list[str] = []

    async def writer(tag: str, delay: float) -> None:
        async with reg.acquire(claims.WriterRole.LIBRARIAN):
            order.append(f"{tag}:in")
            await asyncio.sleep(delay)
            order.append(f"{tag}:out")

    async def driver() -> None:
        await asyncio.gather(writer("A", 0.02), writer("B", 0.00))

    asyncio.run(driver())
    # A entered first; B cannot enter until A exits.
    assert order == ["A:in", "A:out", "B:in", "B:out"]


def test_different_roles_run_in_parallel() -> None:
    claims.reset_registry()
    reg = claims.registry()
    started: list[str] = []

    async def hold(role: claims.WriterRole, tag: str, ev: asyncio.Event) -> None:
        async with reg.acquire(role):
            started.append(tag)
            await ev.wait()

    async def driver() -> None:
        ev = asyncio.Event()
        t1 = asyncio.create_task(hold(claims.WriterRole.LIBRARIAN, "L", ev))
        t2 = asyncio.create_task(hold(claims.WriterRole.BUILDER, "B", ev))
        # Yield until both have started; if locks blocked, only one starts.
        for _ in range(50):
            if len(started) == 2:
                break
            await asyncio.sleep(0.005)
        ev.set()
        await asyncio.gather(t1, t2)

    asyncio.run(driver())
    assert set(started) == {"L", "B"}


# ── Bearer authentication ─────────────────────────────────────────────────────


def test_authenticate_bearer_rejects_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATELIER_MCP_HTTP_TOKEN", raising=False)
    with pytest.raises(PermissionError, match="unset"):
        auth.authenticate_bearer("anything", transport="mcp-http")


def test_authenticate_bearer_rejects_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATELIER_MCP_HTTP_TOKEN", "secret")
    with pytest.raises(PermissionError, match="invalid bearer"):
        auth.authenticate_bearer("nope", transport="mcp-http")


def test_authenticate_bearer_accepts_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATELIER_MCP_HTTP_TOKEN", "secret")
    sess = auth.authenticate_bearer(
        "secret", transport="mcp-http", session_id="abc", working_dir="/tmp"
    )
    assert sess.transport == "mcp-http"
    assert sess.session_id == "abc"
    assert sess.working_dir == "/tmp"
    assert claims.Claim.LIBRARIAN_WRITE in sess.claims
    assert claims.Claim.CAPTOR_WRITE in sess.claims


def test_session_bridges_to_callcontext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATELIER_MCP_HTTP_TOKEN", "secret")
    sess = auth.authenticate_bearer("secret", transport="mcp-http")
    ctx = sess.to_call_context()
    # Bridge returns a CallContext with the same caller + claims; old
    # api.py code paths can use this without knowing about Session.
    assert ctx.caller == sess.caller
    assert ctx.claims == sess.claims


def test_local_cli_session_has_all_claims() -> None:
    sess = auth.local_cli_session()
    for c in claims.Claim:
        assert c in sess.claims
    assert sess.transport == "local-cli"
