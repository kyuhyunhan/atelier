"""Always-on serve (launchd) + resource guardrails.

The guardrails are a SPEC, not prose (the statusline CPU-melt lesson): G2/G3
live in the plist this module renders, G5 in the autosync piggyback reindex.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from runtime.service import daemon as _daemon
from runtime.service import vault_autosync as _autosync


# ── plist spec (G2 crash-loop, G3 low priority) ─────────────────────────────

def test_plist_encodes_the_guardrails() -> None:
    spec = _daemon.render_plist(python_exe="/usr/bin/python3",
                                engine_root=Path("/eng"),
                                log_dir=Path("/logs"))
    assert spec["Label"] == "io.atelier.serve"
    assert spec["KeepAlive"] is True                    # restart on crash …
    assert spec["ThrottleInterval"] == 60               # … at most 1/min (G2)
    assert spec["ProcessType"] == "Background"          # low priority (G3)
    assert spec["Nice"] == 10                           # (G3)
    assert spec["ProgramArguments"][0] == "/usr/bin/python3"
    assert spec["ProgramArguments"][-2:] == ["serve", "--http"]
    assert spec["WorkingDirectory"] == "/eng"
    assert spec["StandardOutPath"].startswith("/logs/")


def test_install_writes_plist_and_loads(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_launchctl(*args):
        calls.append(args)
        class R:  # noqa: N801 - stub
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(_daemon, "_launchctl", fake_launchctl)
    monkeypatch.setattr(_daemon, "plist_path", lambda: tmp_path / "agent.plist")
    monkeypatch.setattr(_daemon, "_log_dir", lambda: tmp_path / "logs")

    out = _daemon.install()
    assert out["loaded"] is True
    assert (tmp_path / "agent.plist").is_file()          # plist written
    assert any(a[0] == "bootstrap" for a in calls)       # agent loaded

    out2 = _daemon.uninstall()
    assert out2["plist_removed"] is True
    assert not (tmp_path / "agent.plist").exists()       # kill switch removes it
    assert any(a[0] == "bootout" for a in calls)


# ── G5: embed cap on the piggyback reindex ──────────────────────────────────

def _fake_status(n: int) -> str:
    return "\n".join(f" M raw/f{i}.md" for i in range(n))


def _capture_reindex(monkeypatch):
    from runtime.index import reindex as _reindex
    seen: Dict[str, object] = {}

    def fake_reindex_space(cfg, name, full=False, **kw):
        seen["embed_gateway"] = kw.get("embed_gateway", "AUTO(default)")
        return _reindex.ReindexStats(space=name)

    monkeypatch.setattr(_reindex, "reindex_space", fake_reindex_space)
    monkeypatch.setattr(_reindex, "canonical_spaces", lambda cfg: ["gorae"])
    return seen


def test_small_commit_keeps_embeddings(atelier_env: Dict, monkeypatch) -> None:
    seen = _capture_reindex(monkeypatch)
    _autosync._reindex_changed(_fake_status(3))          # 3 ≤ cap(50)
    assert seen["embed_gateway"] == "AUTO(default)"      # auto gateway kept


def test_bulk_commit_skips_embeddings(atelier_env: Dict, monkeypatch) -> None:
    seen = _capture_reindex(monkeypatch)
    _autosync._reindex_changed(_fake_status(51))         # 51 > cap(50)
    assert seen["embed_gateway"] is None                 # G5: vectors deferred


# ── session-anchored daemon (default: ensure/stop, no launchd) ──────────────


def _pidfile(atelier_env: Dict) -> Path:
    return atelier_env["cache"].parent / "serve.pid"


def test_ensure_spawns_when_nothing_running(atelier_env: Dict, monkeypatch) -> None:
    calls = []

    class FakeProc:
        pid = 4242

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProc()

    monkeypatch.setattr(_daemon.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(_daemon, "_log_dir", lambda: _pidfile(atelier_env).parent)

    out = _daemon.ensure()
    assert out == {"started": True, "already_running": False, "pid": 4242}
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][-2:] == ["serve", "--http"]           # G1: same entrypoint
    assert kwargs["start_new_session"] is True            # G2: detached, no restart-on-crash


def test_ensure_is_a_noop_when_already_running(atelier_env: Dict, monkeypatch) -> None:
    # "Running" is decided by the flock, not the pidfile's content (a reused
    # pid must never look alive) — so simulate it by actually holding the
    # lock, the same way a real `serve` process would.
    pf = _daemon._server._acquire_pidfile()
    calls = []
    monkeypatch.setattr(_daemon.subprocess, "Popen",
                        lambda *a, **kw: calls.append((a, kw)))
    try:
        out = _daemon.ensure()
        assert out == {"started": False, "already_running": True, "pid": os.getpid()}
        assert calls == []                                 # G1: no duplicate spawn
    finally:
        _daemon._server._release_pidfile(pf)


def test_stop_signals_the_running_pid(atelier_env: Dict, monkeypatch) -> None:
    pf = _daemon._server._acquire_pidfile()
    pf.write_text("9999")          # the pidfile's displayed pid (best-effort)

    killed = []
    monkeypatch.setattr(_daemon.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    try:
        out = _daemon.stop()
        assert out == {"stopped": True, "was_running": True, "pid": 9999}
        assert killed == [(9999, _daemon.signal.SIGTERM)]
    finally:
        _daemon._server._release_pidfile(pf)


def test_ensure_ignores_a_reused_stale_pid_in_the_file(atelier_env: Dict, monkeypatch) -> None:
    """A clean stop leaves the pidfile's content stale on purpose (see
    server._release_pidfile). If that stale pid gets reused by some
    unrelated, currently-alive OS process, `ensure` must still spawn — it
    asks the flock, never `kill(pid, 0)` on content it can't trust."""
    pf = _pidfile(atelier_env)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(os.getpid()))            # our own pid: definitely alive,
                                                # but the lock is free — nobody holds it
    calls = []

    class FakeProc:
        pid = 4242

    monkeypatch.setattr(_daemon.subprocess, "Popen",
                        lambda *a, **kw: (calls.append((a, kw)), FakeProc())[1])
    monkeypatch.setattr(_daemon, "_log_dir", lambda: pf.parent)

    out = _daemon.ensure()
    assert out == {"started": True, "already_running": False, "pid": 4242}
    assert len(calls) == 1


def test_stop_is_a_noop_when_nothing_running(atelier_env: Dict) -> None:
    out = _daemon.stop()
    assert out == {"stopped": False, "was_running": False}
