"""Human/machine commit separation (2026-07): raw/ and the engine tree land as
separate, path-scoped commits — "journal:" vs the machine prefix — so the
diary's git history stays human and the machine's extractions are reviewable
in isolation. Same repo, same durability."""
from __future__ import annotations

import subprocess
from pathlib import Path

from runtime.sync.adapters import github as _gh


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "vault"
    (repo / "raw").mkdir(parents=True)
    (repo / "graph").mkdir(parents=True)
    _git(repo, "init")
    (repo / "seed.md").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    return repo


def _log_subjects(repo: Path) -> list:
    return _git(repo, "log", "--format=%s").strip().splitlines()


def test_split_makes_two_scoped_commits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "raw" / "diary.md").write_text("human words\n")
    (repo / "graph" / "claim.md").write_text("machine claim\n")

    shas = _gh.commit_split(repo, "raw")
    assert len(shas) == 2

    subjects = _log_subjects(repo)
    assert subjects[1].startswith("journal:")            # human commit first
    assert subjects[0].startswith("chore(vault):")       # machine commit second
    # each commit contains ONLY its tree
    human_files = _git(repo, "show", "--name-only", "--format=", shas[0]).split()
    machine_files = _git(repo, "show", "--name-only", "--format=", shas[1]).split()
    assert human_files == ["raw/diary.md"]
    assert machine_files == ["graph/claim.md"]


def test_split_skips_clean_trees(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "raw" / "only-human.md").write_text("x\n")
    shas = _gh.commit_split(repo, "raw")
    assert len(shas) == 1                                # machine pass no-ops
    assert _log_subjects(repo)[0].startswith("journal:")

    (repo / "graph" / "only-machine.md").write_text("y\n")
    shas = _gh.commit_split(repo, "raw")
    assert len(shas) == 1                                # human pass no-ops
    assert _log_subjects(repo)[0].startswith("chore(vault):")

    assert _gh.commit_split(repo, "raw") == []           # fully clean → no-op


def test_machine_pass_catches_root_level_files(tmp_path: Path) -> None:
    # manifests etc. at the vault root belong to the machine commit.
    repo = _repo(tmp_path)
    (repo / ".atelier-vault.yaml").write_text("structure_version: 7\n")
    shas = _gh.commit_split(repo, "raw")
    assert len(shas) == 1
    assert _log_subjects(repo)[0].startswith("chore(vault):")
