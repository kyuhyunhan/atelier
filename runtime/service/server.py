"""atelier serve — long-running asyncio supervisor.

In v0.2 this hosts one or more transports (MCP stdio, MCP HTTP) that all
funnel into runtime.service.api. v0.1's CLI one-shot path is unchanged;
`atelier serve` is the new entry for agents (Claude Code today) to attach.

The supervisor is deliberately small: it opens the shared SQLite
connection, schedules registered transport tasks via asyncio.gather, and
shuts down cleanly on SIGINT / SIGTERM. Transport modules are wired in
later PRs (PR-3 adds mcp_stdio, PR-4 adds mcp_http).
"""
from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from ..util import config as _config
from ..util import db as _db
from ..util import logging as log


TransportTask = Callable[["Supervisor"], Awaitable[None]]


@dataclass
class Supervisor:
    """State shared across transports for a single `atelier serve` process."""
    cfg: _config.Config
    shutdown: asyncio.Event

    @property
    def alive(self) -> bool:
        return not self.shutdown.is_set()


_TRANSPORTS: List[TransportTask] = []


def register_transport(task: TransportTask) -> None:
    """Append a transport task. Called by mcp_stdio / mcp_http on import."""
    _TRANSPORTS.append(task)


async def _idle(sup: Supervisor) -> None:
    """Fallback task so the loop has at least one awaitable to gather on
    when no transports are registered (PR-1 scaffold case)."""
    await sup.shutdown.wait()


async def _run(transports: List[TransportTask]) -> int:
    cfg = _config.load()
    _db.connect_shared()  # warm and migrate

    sup = Supervisor(cfg=cfg, shutdown=asyncio.Event())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, sup.shutdown.set)
        except NotImplementedError:
            # add_signal_handler is unavailable on Windows; tests use a
            # direct sup.shutdown.set() instead.
            pass

    tasks = [asyncio.create_task(t(sup)) for t in transports] or [
        asyncio.create_task(_idle(sup))
    ]
    log.info("serve.ready", transports=len(transports))

    try:
        await sup.shutdown.wait()
    finally:
        log.info("serve.draining", tasks=len(tasks))
        for t in tasks:
            t.cancel()
        # Wait for tasks to finish cancelling so SQLite writes flush.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                log.warn("transport.error", err=type(r).__name__, msg=str(r))
        _db.close_shared()
        log.info("serve.stopped")
    return 0


def run(transports: Optional[List[TransportTask]] = None) -> int:
    """Synchronous entry. CLI calls this; tests call _run() directly."""
    return asyncio.run(_run(list(transports) if transports is not None
                            else list(_TRANSPORTS)))
