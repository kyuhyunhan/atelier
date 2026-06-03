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
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from ..util import config as _config
from ..util import db as _db
from ..util import logging as log


TransportTask = Callable[["Supervisor"], Awaitable[None]]


# ── single-instance guard (pidfile) ────────────────────────────────────────


def _pidfile_path() -> Path:
    return Path(_config.CACHE_DIR).parent / "serve.pid"   # ~/.atelier/serve.pid


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)               # signal 0 = liveness probe, no-op
    except ProcessLookupError:
        return False
    except PermissionError:           # exists but owned by another user
        return True
    return True


class AlreadyRunning(RuntimeError):
    """A live `atelier serve` already holds the pidfile."""

    def __init__(self, pid: int) -> None:
        super().__init__(
            f"atelier serve is already running (pid {pid}). "
            f"Stop it first (kill {pid}) or use the existing instance."
        )
        self.pid = pid


def _acquire_pidfile() -> Path:
    """Claim the single-instance pidfile. Raises AlreadyRunning if a live
    process holds it; reclaims a stale pidfile (process gone)."""
    pf = _pidfile_path()
    if pf.exists():
        try:
            existing = int(pf.read_text().strip() or "0")
        except (ValueError, OSError):
            existing = 0
        if existing and existing != os.getpid() and _pid_alive(existing):
            raise AlreadyRunning(existing)
        # stale (dead pid / unparsable) → reclaim
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(os.getpid()))
    return pf


def _release_pidfile(pf: Path) -> None:
    try:
        if pf.exists() and pf.read_text().strip() == str(os.getpid()):
            pf.unlink()
    except OSError:                   # pragma: no cover
        pass


@dataclass
class Supervisor:
    """State shared across transports for a single `atelier serve` process."""
    cfg: _config.Config
    shutdown: asyncio.Event

    @property
    def alive(self) -> bool:
        return not self.shutdown.is_set()


_TRANSPORTS: List[TransportTask] = []
_BACKGROUNDS: List[TransportTask] = []


def register_transport(task: TransportTask) -> None:
    """Append a transport task. Called by mcp_stdio / mcp_http on import."""
    _TRANSPORTS.append(task)


def register_background(task: TransportTask) -> None:
    """Append a background subsystem task (e.g. the vault auto-sync poller).

    Unlike transports, background tasks accept no external connections — they
    run internal work on the supervisor's loop. Idempotent so repeated imports
    don't double-register."""
    if task not in _BACKGROUNDS:
        _BACKGROUNDS.append(task)


async def _idle(sup: Supervisor) -> None:
    """Fallback task so the loop has at least one awaitable to gather on
    when no transports are registered (PR-1 scaffold case)."""
    await sup.shutdown.wait()


async def _run(transports: List[TransportTask]) -> int:
    cfg = _config.load()
    log.configure()                   # defensive: ensure the file sink exists
    _db.connect_shared()  # warm and migrate

    pidfile = _acquire_pidfile()      # raises AlreadyRunning if a live serve exists
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
        _release_pidfile(pidfile)
        log.info("serve.stopped")
    return 0


def run(transports: Optional[List[TransportTask]] = None) -> int:
    """Synchronous entry. CLI calls this; tests call _run() directly.

    Returns 0 on clean shutdown, 3 if another instance already holds the
    port (friendly message instead of an uvicorn stack trace)."""
    base = list(transports) if transports is not None else list(_TRANSPORTS)
    try:
        return asyncio.run(_run(base + list(_BACKGROUNDS)))
    except AlreadyRunning as e:
        log.error("serve.already-running", detail=str(e))
        return 3
