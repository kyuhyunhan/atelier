"""PR-41: single-instance guard (pidfile) for `atelier serve`."""
from __future__ import annotations

import asyncio
import os
from typing import Dict

import pytest

from runtime.service import server as _server


def test_acquire_then_release(atelier_env: Dict) -> None:
    pf = _server._acquire_pidfile()
    assert pf.exists()
    assert pf.read_text().strip() == str(os.getpid())
    _server._release_pidfile(pf)
    assert not pf.exists()


def test_second_acquire_with_live_pid_raises(atelier_env: Dict) -> None:
    pf = _server._pidfile_path()
    pf.parent.mkdir(parents=True, exist_ok=True)
    # our own pid is alive but != getpid()? use parent process which is alive.
    # Simplest: write a definitely-alive pid that isn't ours → use os.getppid().
    other = os.getppid()
    pf.write_text(str(other))
    if other == os.getpid():            # pragma: no cover - defensive
        pytest.skip("ppid == pid")
    with pytest.raises(_server.AlreadyRunning) as ei:
        _server._acquire_pidfile()
    assert ei.value.pid == other


def test_stale_pidfile_is_reclaimed(atelier_env: Dict) -> None:
    pf = _server._pidfile_path()
    pf.parent.mkdir(parents=True, exist_ok=True)
    # a pid that is essentially never alive
    pf.write_text("2147480000")
    reclaimed = _server._acquire_pidfile()   # should NOT raise
    assert reclaimed.read_text().strip() == str(os.getpid())
    _server._release_pidfile(reclaimed)


def test_unparsable_pidfile_is_reclaimed(atelier_env: Dict) -> None:
    pf = _server._pidfile_path()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text("not-a-pid\n")
    reclaimed = _server._acquire_pidfile()
    assert reclaimed.read_text().strip() == str(os.getpid())
    _server._release_pidfile(reclaimed)


def test_run_returns_3_when_already_running(atelier_env: Dict) -> None:
    """run() surfaces a friendly exit code 3 instead of a stack trace when
    a live instance holds the pidfile."""
    pf = _server._pidfile_path()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(os.getppid()))         # a live, non-self pid

    rc = _server.run(transports=[])          # would idle; should bail early
    assert rc == 3


def test_run_releases_pidfile_on_clean_exit(atelier_env: Dict) -> None:
    """A normal serve run acquires then releases the pidfile."""
    async def stopper(sup: _server.Supervisor) -> None:
        sup.shutdown.set()

    rc = _server.run(transports=[stopper])
    assert rc == 0
    assert not _server._pidfile_path().exists()
