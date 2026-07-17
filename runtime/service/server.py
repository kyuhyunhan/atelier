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
import errno
import fcntl
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from ..util import config as _config
from ..util import db as _db
from ..util import logging as log


TransportTask = Callable[["Supervisor"], Awaitable[None]]


# ── single-instance guard (pidfile, kernel-arbitrated via flock) ───────────
#
# G1 (single instance) now has genuinely concurrent callers: the
# session-anchored daemon (`daemon.ensure()`) can be invoked by several
# Claude Code sessions starting at once, each backgrounding `atelier daemon
# ensure` independently. A plain exists()→read→write pidfile is a TOCTOU
# race under that concurrency — two callers can both pass the liveness
# check before either writes, and both proceed into an uvicorn port bind.
# flock(LOCK_EX|LOCK_NB) makes the claim atomic: the kernel serializes it,
# so exactly one caller wins and every loser gets a clean AlreadyRunning.


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
        detail = (f"kill {pid}" if pid else "wait a moment and retry — it may "
                  f"still be writing its pid")
        super().__init__(
            f"atelier serve is already running"
            f"{f' (pid {pid})' if pid else ''}. "
            f"Stop it first ({detail}) or use the existing instance."
        )
        self.pid = pid


_HELD_FDS: dict = {}


def _acquire_pidfile() -> Path:
    """Claim the single-instance pidfile via an atomic, kernel-arbitrated
    flock. Raises AlreadyRunning if a live process holds it; reclaims a
    stale pidfile (lock free, whatever pid is written) automatically —
    flock releases when a dead process's fd closes, no manual staleness
    check needed."""
    pf = _pidfile_path()
    pf.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pf, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno not in (errno.EACCES, errno.EAGAIN):
            os.close(fd)
            raise
        os.close(fd)
        try:
            existing = int(pf.read_text().strip() or "0")
        except (ValueError, OSError):
            existing = 0
        raise AlreadyRunning(existing)
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    os.fsync(fd)
    _HELD_FDS[pf] = fd
    return pf


def _release_pidfile(pf: Path) -> None:
    """Drop the flock. Deliberately does NOT unlink the path: flock is a
    per-inode lock, not a per-path one, so unlinking after unlocking would
    reopen a TOCTOU window — a third process could create+lock a fresh
    inode at the same path while a just-released-but-still-alive locker's
    fd (from a raced-in acquirer) still points at the old one. Leaving
    stale content behind is harmless — staleness is decided purely by
    whether the flock is free, per _acquire_pidfile, never by content."""
    fd = _HELD_FDS.pop(pf, None)
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:               # pragma: no cover
            pass
        os.close(fd)                  # closing drops the flock either way


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
