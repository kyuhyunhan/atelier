"""Phase 1 — synchronous git commit primitive + safety guards.

These tests exercise the adapter and orchestrator against REAL temporary
git repositories (no network): a bare repo stands in for the remote so
`push` succeeds locally. The auto-sync feature is built on top of these
primitives in later phases.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from runtime.sync import orchestrator
from runtime.sync.adapters import github
from runtime.util.config import Config, VaultConfig


# ── helpers ────────────────────────────────────────────────────────────────


def _run(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args],
                                   stderr=subprocess.STDOUT, text=True)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q")
    _run(path, "config", "user.email", "test@example.com")
    _run(path, "config", "user.name", "Test")
    _run(path, "branch", "-M", "main")
    (path / "seed.md").write_text("seed\n")
    _run(path, "add", "-A")
    _run(path, "commit", "-q", "-m", "seed")
    return path


def _attach_bare_remote(repo: Path, bare: Path) -> Path:
    _run(bare.parent, "init", "--bare", "-q", str(bare))
    _run(repo, "remote", "add", "origin", str(bare))
    _run(repo, "push", "-q", "-u", "origin", "main")
    return bare


def _vault_cfg(local: Path) -> Config:
    return Config(
        spaces={}, raw={},
        vault=VaultConfig(local=local, remote_type="github",
                          remote_url="local-bare", remote_branch="main"),
    )


# ── adapter: commit ─────────────────────────────────────────────────────────


def test_commit_creates_commit_when_dirty(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "new.md").write_text("hello\n")

    sha = github.commit(repo, "chore(vault): test")

    assert sha and sha != "nothing to commit"
    assert _run(repo, "rev-parse", "HEAD").strip() == sha
    assert "new.md" in _run(repo, "show", "--name-only", "--format=", "HEAD")


def test_commit_noop_when_clean(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    head_before = _run(repo, "rev-parse", "HEAD").strip()

    result = github.commit(repo, "chore(vault): test")

    assert result == "nothing to commit"
    assert _run(repo, "rev-parse", "HEAD").strip() == head_before


def test_commit_stages_untracked_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "sub").mkdir()
    (repo / "sub" / "a.md").write_text("a\n")

    github.commit(repo, "chore(vault): test")

    assert "sub/a.md" in _run(repo, "show", "--name-only", "--format=", "HEAD")


# ── adapter: safety predicates ───────────────────────────────────────────────


def test_is_repo_root_true_at_toplevel(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert github.is_repo_root(repo) is True


def test_is_repo_root_false_in_subdir(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    sub = repo / "nested"
    sub.mkdir()
    assert github.is_repo_root(sub) is False


def test_is_repo_root_false_when_not_a_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert github.is_repo_root(plain) is False


def test_in_merge_or_rebase_detects_merge_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert github.in_merge_or_rebase(repo) is False
    (repo / ".git" / "MERGE_HEAD").write_text("deadbeef\n")
    assert github.in_merge_or_rebase(repo) is True


def test_in_merge_or_rebase_detects_cherry_pick(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".git" / "CHERRY_PICK_HEAD").write_text("deadbeef\n")
    assert github.in_merge_or_rebase(repo) is True


def test_lock_present_detects_index_lock(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert github.lock_present(repo) is False
    (repo / ".git" / "index.lock").write_text("")
    assert github.lock_present(repo) is True


# ── orchestrator: commit_push ────────────────────────────────────────────────


def test_commit_push_happy_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    bare = _attach_bare_remote(repo, tmp_path / "bare.git")
    (repo / "note.md").write_text("note\n")

    out = orchestrator.commit_push(_vault_cfg(repo), message="chore(vault): sync")

    assert out["committed"] is True
    # remote received it (bare repo: query the pushed branch explicitly)
    remote_log = _run(bare, "log", "--format=%s", "-1", "main").strip()
    assert remote_log == "chore(vault): sync"


def test_commit_push_targets_vault_once_not_pseudospaces(tmp_path: Path) -> None:
    """vault mode synthesizes two pseudo-spaces pointing at the same dir;
    commit_push must act on the vault dir exactly once (one commit)."""
    repo = _init_repo(tmp_path / "repo")
    _attach_bare_remote(repo, tmp_path / "bare.git")
    (repo / "note.md").write_text("note\n")

    before = len(_run(repo, "log", "--format=%h").strip().splitlines())
    orchestrator.commit_push(_vault_cfg(repo), message="chore(vault): sync")
    after = len(_run(repo, "log", "--format=%h").strip().splitlines())

    assert after - before == 1


def test_commit_push_skips_when_mid_merge(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _attach_bare_remote(repo, tmp_path / "bare.git")
    (repo / "note.md").write_text("note\n")
    (repo / ".git" / "MERGE_HEAD").write_text("deadbeef\n")

    head_before = _run(repo, "rev-parse", "HEAD").strip()
    out = orchestrator.commit_push(_vault_cfg(repo), message="chore(vault): sync")

    assert out["committed"] is False
    assert out.get("skipped") == "mid-merge-or-lock"
    assert _run(repo, "rev-parse", "HEAD").strip() == head_before


def test_commit_push_skips_when_not_repo_root(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    sub = repo / "nested"
    sub.mkdir()
    (sub / "note.md").write_text("note\n")

    out = orchestrator.commit_push(_vault_cfg(sub), message="chore(vault): sync")

    assert out["committed"] is False
    assert out.get("skipped") == "not-repo-root"


def test_commit_push_push_failure_is_caught_not_raised(tmp_path: Path) -> None:
    """Local commit succeeds; push to an unreachable remote is caught and
    surfaced, never propagated (must not crash the caller / watcher)."""
    repo = _init_repo(tmp_path / "repo")
    # origin points nowhere reachable, with an upstream so `git push` tries.
    bogus = tmp_path / "does-not-exist.git"
    _run(repo, "remote", "add", "origin", str(bogus))
    # fabricate upstream tracking without a successful push
    _run(repo, "config", "branch.main.remote", "origin")
    _run(repo, "config", "branch.main.merge", "refs/heads/main")
    (repo / "note.md").write_text("note\n")

    out = orchestrator.commit_push(_vault_cfg(repo), message="chore(vault): sync",
                                   timeout=10)

    assert out["committed"] is True          # local commit happened
    assert out["pushed"] is False            # push failed
    assert out.get("push_error")             # surfaced, not raised
    # local HEAD advanced even though push failed
    assert _run(repo, "log", "--format=%s", "-1").strip() == "chore(vault): sync"


# ── api layer: action routing ────────────────────────────────────────────────


def test_api_sync_commit_push_routes_to_orchestrator(tmp_path: Path,
                                                      monkeypatch) -> None:
    repo = _init_repo(tmp_path / "repo")
    _attach_bare_remote(repo, tmp_path / "bare.git")
    (repo / "note.md").write_text("note\n")

    from runtime.util import config as _config
    from runtime.service import api
    monkeypatch.setattr(_config, "load", lambda *a, **k: _vault_cfg(repo))

    out = api.sync("commit-push")

    assert out["committed"] is True
    assert out["pushed"] is True
    # default subject was applied
    assert _run(repo, "log", "--format=%s", "-1", "main").strip() == \
        "chore(vault): sync [auto]"


def test_cli_sync_commit_push(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _init_repo(tmp_path / "repo")
    _attach_bare_remote(repo, tmp_path / "bare.git")
    (repo / "note.md").write_text("note\n")

    from runtime.util import config as _config
    from runtime import cli
    monkeypatch.setattr(_config, "load", lambda *a, **k: _vault_cfg(repo))

    rc = cli.main(["sync", "commit-push", "--message", "chore(vault): manual"])

    assert rc == 0
    assert "committed" in capsys.readouterr().out
    assert _run(repo, "log", "--format=%s", "-1", "main").strip() == \
        "chore(vault): manual"
