"""PR-41/PR-51: single-instance guard (pidfile) `atelier serve`.

The guard is a kernel-arbitrated flock (LOCK_EX|LOCK_NB) on the pidfile, not
a read-check-write on its contents. That's what makes concurrent callers
(the session-anchored daemon can be started by several Claude Code sessions
at once) resolve deterministically: exactly one wins the flock, everyone
else gets a clean AlreadyRunning instead of racing into a port bind."""
from __future__ import annotations

import os
import signal
from typing import Dict

import pytest

from runtime.service import server as _server


def _fork_holder(w: int):
    """Fork a child that acquires the pidfile flock, writes b"ok"/b"no" to
    the pipe, then blocks until killed. Returns the child pid."""
    pid = os.fork()
    if pid == 0:
        try:
            _server._acquire_pidfile()
            os.write(w, b"ok")
        except BaseException:
            os.write(w, b"no")
        finally:
            os.close(w)
        signal.pause()
        os._exit(0)
    return pid


def test_acquire_then_release(atelier_env: Dict) -> None:
    pf = _server._acquire_pidfile()
    assert pf.exists()
    assert pf.read_text().strip() == str(os.getpid())
    _server._release_pidfile(pf)
    assert not pf.exists()


def test_concurrent_acquire_second_gets_already_running(atelier_env: Dict) -> None:
    """A child process holds the flock; the parent's acquire must lose
    cleanly (AlreadyRunning), never race into overwriting the pidfile."""
    pf = _server._pidfile_path()
    r, w = os.pipe()
    child_pid = _fork_holder(w)
    os.close(w)
    ready = os.read(r, 2)
    os.close(r)
    try:
        assert ready == b"ok"
        with pytest.raises(_server.AlreadyRunning) as ei:
            _server._acquire_pidfile()
        assert ei.value.pid == child_pid
    finally:
        os.kill(child_pid, signal.SIGKILL)
        os.waitpid(child_pid, 0)
        if pf.exists():
            pf.unlink()


def test_leftover_pidfile_with_no_lock_is_reclaimed(atelier_env: Dict) -> None:
    """A pidfile can outlive the process that wrote it (crash, kill -9) —
    since no one holds the flock, acquiring it is always safe regardless of
    what stale content is sitting in the file."""
    pf = _server._pidfile_path()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text("2147480000")
    reclaimed = _server._acquire_pidfile()
    assert reclaimed.read_text().strip() == str(os.getpid())
    _server._release_pidfile(reclaimed)


def test_unparsable_pidfile_is_reclaimed(atelier_env: Dict) -> None:
    pf = _server._pidfile_path()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text("not-a-pid\n")
    reclaimed = _server._acquire_pidfile()
    assert reclaimed.read_text().strip() == str(os.getpid())
    _server._release_pidfile(reclaimed)


def test_run_returns_3_when_flock_held(atelier_env: Dict) -> None:
    pf = _server._pidfile_path()
    pf.parent.mkdir(parents=True, exist_ok=True)
    r, w = os.pipe()
    child_pid = _fork_holder(w)
    os.close(w)
    ready = os.read(r, 2)
    os.close(r)
    try:
        assert ready == b"ok"
        assert _server.run(transports=[]) == 3
    finally:
        os.kill(child_pid, signal.SIGKILL)
        os.waitpid(child_pid, 0)
        if pf.exists():
            pf.unlink()


def test_run_releases_pidfile_on_clean_shutdown(atelier_env: Dict) -> None:
    async def stopper(sup: _server.Supervisor) -> None:
        sup.shutdown.set()

    rc = _server.run(transports=[stopper])
    assert rc == 0
    assert not _server._pidfile_path().exists()
