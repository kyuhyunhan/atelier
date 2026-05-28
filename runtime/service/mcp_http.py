"""MCP HTTP transport — Streamable HTTP, bound to loopback only.

Claude Code (and Claude.ai chat custom connectors) attach to this
endpoint via the MCP HTTP transport. A static bearer token from
`~/.atelier/secrets/.env` gates access; the bind address is forced to
127.0.0.1 / ::1 at construction time.

The transport task is registered with the supervisor on import. v0.2 is
deliberately single-user, loopback-only; remote exposure with OAuth is
deferred to v0.3.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Optional

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "The 'mcp' package is required for the MCP HTTP transport. "
        "Install with `pip install -e '.[serve]'`."
    ) from e

import uvicorn  # type: ignore[import-not-found]
from starlette.requests import Request  # type: ignore[import-not-found]
from starlette.responses import JSONResponse  # type: ignore[import-not-found]
from starlette.types import ASGIApp, Receive, Scope, Send  # type: ignore[import-not-found]

from ..util import config as _config
from ..util import logging as log
from . import auth, server as _server, tools as _tools


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_DEFAULT_PORT = 7322
_DEFAULT_BIND = "127.0.0.1"
_TOKEN_ENV = "ATELIER_MCP_HTTP_TOKEN"


def _resolve_settings(cfg: _config.Config) -> tuple[str, int, str]:
    """Pull (bind, port, token_env) from config; refuse non-loopback."""
    svc = (cfg.raw.get("service") or {}).get("mcp_http") or {}
    bind = svc.get("bind", _DEFAULT_BIND)
    port = int(svc.get("port", _DEFAULT_PORT))
    token_env = svc.get("token_env", _TOKEN_ENV)
    if bind not in _LOOPBACK:
        raise ValueError(
            f"service.mcp_http.bind must be loopback ({sorted(_LOOPBACK)}); "
            f"got {bind!r}. atelier refuses non-loopback HTTP exposure in v0.2."
        )
    return bind, port, token_env


class BearerMiddleware:
    """ASGI middleware: validates Authorization header, sets Session.

    On success, swaps the tools._current contextvar to a bearer-authenticated
    Session for the duration of the request. On failure, returns 401.

    A small number of paths can be unauthenticated (health probes); we
    deliberately do not expose any in v0.2.
    """

    def __init__(self, app: ASGIApp, *, token_env: str) -> None:
        self.app = app
        self.token_env = token_env

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        header = request.headers.get("authorization", "")
        token: Optional[str] = None
        if header.lower().startswith("bearer "):
            token = header.split(" ", 1)[1].strip()

        try:
            sess = auth.authenticate_bearer(
                token,
                transport="mcp-http",
                session_id=request.headers.get("mcp-session-id"),
                working_dir=request.headers.get("x-atelier-working-dir"),
                env_var=self.token_env,
            )
        except PermissionError as e:
            response = JSONResponse({"error": str(e)}, status_code=401)
            await response(scope, receive, send)
            return

        ctx_token = _tools.set_session(sess)
        try:
            await self.app(scope, receive, send)
        finally:
            _tools._current.reset(ctx_token)


def build_app(cfg: _config.Config) -> tuple[FastMCP, ASGIApp, str, int]:
    """Construct FastMCP + middleware-wrapped ASGI app. Returns the bind
    and port too so the caller can hand them to uvicorn.
    """
    bind, port, token_env = _resolve_settings(cfg)

    fmcp = FastMCP(
        name="atelier",
        instructions=(
            "atelier MCP HTTP transport (localhost, bearer-authenticated). "
            "Same tool surface as the stdio transport."
        ),
        host=bind,
        port=port,
    )
    _tools.add_to_fastmcp(fmcp)

    asgi = fmcp.streamable_http_app()
    asgi = BearerMiddleware(asgi, token_env=token_env)
    return fmcp, asgi, bind, port


async def run(sup: _server.Supervisor) -> None:
    """Transport task: serves uvicorn until supervisor shuts down."""
    _fmcp, app, bind, port = build_app(sup.cfg)

    if bind not in _LOOPBACK:  # double-check (constructor already raised)
        raise ValueError(f"refusing non-loopback bind: {bind}")

    server_config = uvicorn.Config(
        app=app,
        host=bind,
        port=port,
        log_level="warning",
        lifespan="on",     # FastMCP needs the session manager lifespan.
        access_log=False,
    )
    uv = uvicorn.Server(server_config)

    log.info("mcp-http.ready", bind=bind, port=port,
             tools=len(_tools.iter_tools()))

    serve_task = asyncio.create_task(uv.serve())
    try:
        # Wait until either the supervisor signals shutdown or uvicorn
        # exits unexpectedly.
        done, _pending = await asyncio.wait(
            [serve_task, asyncio.create_task(sup.shutdown.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if serve_task not in done:
            uv.should_exit = True
            await serve_task
    finally:
        uv.should_exit = True


# Auto-register on import (cli's `serve --http` triggers this).
_server.register_transport(run)
