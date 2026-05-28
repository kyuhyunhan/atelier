"""MCP stdio transport — used by Claude Code when launched as a subprocess.

This module is opt-in: importing it registers a transport task with
runtime.service.server. The task runs FastMCP's stdio loop until stdin
closes (or the supervisor's shutdown event fires), then signals the
supervisor to stop.

Logger output goes to stderr only when this transport is active —
stdin/stdout belong to the JSON-RPC frames.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
except ImportError as e:  # pragma: no cover - import guard
    raise ImportError(
        "The 'mcp' package is required for the MCP stdio transport. "
        "Install with `pip install -e '.[serve]'`."
    ) from e

from ..util import logging as log
from . import auth, server as _server, tools as _tools


def build_app() -> FastMCP:
    """Construct a FastMCP server with every registered tool."""
    app = FastMCP(
        name="atelier",
        instructions=(
            "atelier — sovereign personal memory engine. Tools operate "
            "on the user's vault (markdown + SQLite projection). Read "
            "tools require no claim; write tools require the matching "
            "writer claim and serialize per writer-role."
        ),
    )
    _tools.add_to_fastmcp(app)
    return app


async def run(sup: _server.Supervisor) -> None:
    """Transport task: runs the FastMCP stdio loop until peer disconnects."""
    # Stdio callers are local subprocesses — full trust.
    _tools.set_session(auth.local_cli_session())
    app = build_app()
    log.info("mcp-stdio.ready", tools=len(_tools.iter_tools()))

    try:
        # FastMCP's run_stdio_async blocks until stdin closes.
        await app.run_stdio_async()
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # pragma: no cover - transport-level
        log.error("mcp-stdio.crash", err=type(e).__name__, msg=str(e))
        raise
    finally:
        sup.shutdown.set()


# Auto-register at import time so cli `serve --stdio` picks it up.
_server.register_transport(run)
