"""Phase 3 — vault auto-sync poll subsystem.

The per-tick decision (`_decide`) is pure and tested exhaustively without
async. The loop (`_poll_loop`) is driven with injected status/commit/lock/
sleep callables and a fake clock-free sleeper, so no real time elapses.
"""
from __future__ import annotations

import asyncio
from typing import List

import pytest

from runtime.service import server, vault_autosync as vas


# ── pure decision core ───────────────────────────────────────────────────────


def test_decide_clean_tree_no_commit() -> None:
    assert vas._decide("", "M f", require_stable=True, lock_busy=False) == (False, None)


def test_decide_first_dirty_tick_defers_for_stability() -> None:
    # prev is None, status is dirty → record fingerprint, don't commit yet
    assert vas._decide("M a", None, require_stable=True, lock_busy=False) == (False, "M a")


def test_decide_settled_dirty_commits() -> None:
    # same fingerprint as last tick → quiescent → commit
    assert vas._decide("M a", "M a", require_stable=True, lock_busy=False) == (True, None)


def test_decide_changed_dirty_waits_again() -> None:
    assert vas._decide("M b", "M a", require_stable=True, lock_busy=False) == (False, "M b")


def test_decide_lock_busy_defers_keeping_memory() -> None:
    assert vas._decide("M a", "M a", require_stable=True, lock_busy=True) == (False, "M a")


def test_decide_no_stability_requirement_commits_immediately() -> None:
    assert vas._decide("M a", None, require_stable=False, lock_busy=False) == (True, None)


# ── message builder ──────────────────────────────────────────────────────────


def test_message_counts_changes_and_lists_paths() -> None:
    status = " M wiki/a.md\n?? raw/b.md"
    msg = vas._message("chore(vault):", status)
    assert msg.splitlines()[0] == "chore(vault): sync 2 change(s) [auto]"
    assert "wiki/a.md" in msg and "raw/b.md" in msg


def test_message_no_co_author_line() -> None:
    msg = vas._message("chore(vault):", " M a.md")
    assert "Co-Authored-By" not in msg and "Claude" not in msg


# ── loop wiring (injected, no real time/git) ─────────────────────────────────


class _FakeSup:
    def __init__(self) -> None:
        self.shutdown = asyncio.Event()


def _sleeper(n_ticks: int):
    """Returns a sleep_fn that proceeds n_ticks times then signals stop."""
    calls = {"n": 0}

    async def sleep_fn(_interval: float) -> bool:
        if calls["n"] >= n_ticks:
            return False
        calls["n"] += 1
        return True
    return sleep_fn


def _drive(*, statuses: List[str], require_stable: bool, lock_busy: bool = False,
           reindex_fn=None):
    sup = _FakeSup()
    commits: List[str] = []
    seq = iter(statuses)

    def status_fn() -> str:
        try:
            return next(seq)
        except StopIteration:
            return ""

    async def go() -> None:
        await vas._poll_loop(
            sup,
            status_fn=status_fn,
            commit_fn=lambda msg: commits.append(msg),
            lock_busy_fn=lambda: lock_busy,
            sleep_fn=_sleeper(len(statuses)),
            require_stable=require_stable,
            interval_seconds=0.0,
            message_prefix="chore(vault):",
            reindex_fn=reindex_fn,
        )

    asyncio.run(go())
    return commits


def test_loop_commits_once_when_dirty_settles() -> None:
    # dirty, same fingerprint twice → one commit, then clean ticks
    commits = _drive(statuses=["M a", "M a", "", ""], require_stable=True)
    assert len(commits) == 1
    assert commits[0].startswith("chore(vault): sync 1 change(s) [auto]")


def test_loop_skips_while_dirty_set_keeps_changing() -> None:
    commits = _drive(statuses=["M a", "M b", "M c"], require_stable=True)
    assert commits == []


def test_loop_skips_while_writer_lock_held() -> None:
    commits = _drive(statuses=["M a", "M a", "M a"], require_stable=True, lock_busy=True)
    assert commits == []


# ── RFC 0005 §7.2 — reindex piggyback (after a successful commit) ─────────────


def test_loop_reindexes_changed_files_after_commit() -> None:
    """On a settled-dirty tick the loop commits, THEN reindexes — passing the
    committed porcelain (the changed-file set) to the reindex callable."""
    seen: List[str] = []
    commits = _drive(
        statuses=["M a", "M a", "", ""], require_stable=True,
        reindex_fn=lambda status: seen.append(status),
    )
    assert len(commits) == 1
    assert seen == ["M a"]                 # reindexed exactly once, the changed set


def test_loop_does_not_reindex_when_no_commit() -> None:
    """No commit (tree never settles) → no reindex. The quiescence gate that
    gates the commit also gates the reindex — never mid-write."""
    seen: List[str] = []
    commits = _drive(
        statuses=["M a", "M b", "M c"], require_stable=True,
        reindex_fn=lambda status: seen.append(status),
    )
    assert commits == []
    assert seen == []


def test_loop_skips_reindex_when_commit_fails() -> None:
    """A failed commit must not trigger a reindex (nothing was committed) and
    must not crash the loop."""
    sup = _FakeSup()
    seen: List[str] = []
    seq = iter(["M a", "M a", "", ""])

    def status_fn() -> str:
        try:
            return next(seq)
        except StopIteration:
            return ""

    def commit_fn(_msg: str) -> None:
        raise RuntimeError("commit boom")

    async def go() -> None:
        await vas._poll_loop(
            sup, status_fn=status_fn, commit_fn=commit_fn,
            lock_busy_fn=lambda: False, sleep_fn=_sleeper(4),
            require_stable=True, interval_seconds=0.0,
            message_prefix="x:", reindex_fn=lambda s: seen.append(s))

    asyncio.run(go())                      # does not raise
    assert seen == []                      # commit failed → no reindex


def test_loop_reindex_error_does_not_crash_loop() -> None:
    """A reindex hiccup is swallowed (logged) — the poll loop survives and the
    commit still counts."""
    commits = _drive(
        statuses=["M a", "M a", "", ""], require_stable=True,
        reindex_fn=lambda status: (_ for _ in ()).throw(RuntimeError("idx boom")),
    )
    assert len(commits) == 1               # committed; reindex error swallowed


def test_loop_exits_promptly_on_shutdown() -> None:
    sup = _FakeSup()

    async def sleep_fn(_i: float) -> bool:
        return False                     # shutdown already

    async def go() -> int:
        ran = {"ticks": 0}

        def status_fn() -> str:
            ran["ticks"] += 1
            return "M a"

        await vas._poll_loop(
            sup, status_fn=status_fn, commit_fn=lambda m: None,
            lock_busy_fn=lambda: False, sleep_fn=sleep_fn,
            require_stable=True, interval_seconds=0.0, message_prefix="x:")
        return ran["ticks"]

    assert asyncio.run(go()) == 0        # never reached a tick


# ── self-gating run() ────────────────────────────────────────────────────────


def test_run_idles_when_disabled(vault_env, monkeypatch) -> None:
    from runtime.util import config as _config
    cfg = _config.load(vault_env["home"] / "config.yaml")
    assert cfg.auto_sync.enabled is False
    sup = server.Supervisor(cfg=cfg, shutdown=asyncio.Event())

    async def go() -> None:
        task = asyncio.ensure_future(vas.run(sup))
        await asyncio.sleep(0)           # let it reach the idle await
        sup.shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(go())                    # returns cleanly → idled, no crash


def test_run_registered_as_background() -> None:
    assert vas.run in server._BACKGROUNDS


# ── vault access diagnostics (distinguish TCC denial from a real misconfig) ──


def test_access_reason_empty_for_a_real_repo(tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert vas._vault_access_reason(tmp_path) == ""


def test_access_reason_flags_a_non_repo_dir(tmp_path) -> None:
    assert vas._vault_access_reason(tmp_path) == "vault is not a git repo root"


def test_access_reason_flags_missing_path(tmp_path) -> None:
    assert vas._vault_access_reason(tmp_path / "nope") == "vault path does not exist"


def test_access_reason_names_tcc_when_protected(tmp_path, monkeypatch) -> None:
    def deny(_path):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(vas.os, "listdir", deny)

    from runtime.service import daemon as _daemon
    monkeypatch.setattr(_daemon, "is_tcc_protected", lambda p: True)

    reason = vas._vault_access_reason(tmp_path)
    assert "TCC" in reason
    assert "atelier daemon ensure" in reason


def test_access_reason_generic_permission_denied_outside_tcc(tmp_path, monkeypatch) -> None:
    def deny(_path):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(vas.os, "listdir", deny)

    from runtime.service import daemon as _daemon
    monkeypatch.setattr(_daemon, "is_tcc_protected", lambda p: False)

    assert vas._vault_access_reason(tmp_path) == "permission denied reading vault path"


# ── integration: real git repo through the loop to a bare remote ─────────────


def test_integration_loop_commits_and_pushes_real_repo(tmp_path) -> None:
    import subprocess
    from pathlib import Path
    from runtime.sync import orchestrator
    from runtime.sync.adapters import github
    from runtime.util.config import Config, VaultConfig

    def run(cwd: Path, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(cwd), *args],
                                       stderr=subprocess.STDOUT, text=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-q")
    run(repo, "config", "user.email", "t@e.com")
    run(repo, "config", "user.name", "T")
    run(repo, "branch", "-M", "main")
    (repo / "seed.md").write_text("seed\n")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "seed")
    bare = tmp_path / "bare.git"
    run(tmp_path, "init", "--bare", "-q", str(bare))
    run(repo, "remote", "add", "origin", str(bare))
    run(repo, "push", "-q", "-u", "origin", "main")

    # a real, unstaged change in the vault
    (repo / "new.md").write_text("hello\n")

    cfg = Config(spaces={}, raw={},
                 vault=VaultConfig(local=repo, remote_type="github",
                                   remote_url="x", remote_branch="main"))
    sup = _FakeSup()

    async def go() -> None:
        await vas._poll_loop(
            sup,
            status_fn=lambda: github.dirty_porcelain(repo),
            commit_fn=lambda msg: orchestrator.commit_push(
                cfg, message=msg, push=True, on_conflict="surface"),
            lock_busy_fn=lambda: False,
            sleep_fn=_sleeper(4),          # 2 ticks to settle, then clean ticks
            require_stable=True,
            interval_seconds=0.0,
            message_prefix="chore(vault):",
        )

    asyncio.run(go())

    # commit landed locally and on the bare remote
    assert run(repo, "log", "--format=%s", "-1", "main").strip() == \
        "chore(vault): sync 1 change(s) [auto]"
    assert run(bare, "log", "--format=%s", "-1", "main").strip() == \
        "chore(vault): sync 1 change(s) [auto]"
