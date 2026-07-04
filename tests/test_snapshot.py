"""Data-safety snapshot (RFC 0006 P0.2): create is additive; restore rolls the
vault + durables back, and refuses a dirty tree without force."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict

import pytest

from runtime.service import snapshot as _snap


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd), capture_output=True, text=True)


def _init_repo(vault: Path) -> None:
    _git(vault, "init")
    (vault / "seed.md").write_text("seed\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "seed")


def test_create_is_additive_and_records_tag(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _init_repo(vault)
    m = _snap.create()
    assert m["tag"] and m["vault_sha"]                 # git repo → tag + sha
    assert "config.yaml" in m["durables"]              # home durables captured
    # additive: the vault tree is untouched by create.
    assert _git(vault, "status", "--porcelain").stdout.strip() == ""
    assert m in _snap.list_snapshots()


def test_restore_rolls_vault_and_durables_back(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    home = atelier_env["home"]
    _init_repo(vault)
    m = _snap.create()

    # Mutate the vault (committed → clean tree) and a durable.
    (vault / "raw" / "added.md").write_text("added\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "add")
    (home / "config.yaml").write_text("CHANGED\n")

    out = _snap.restore(m["ts"])
    assert out["vault_restored"] is True
    # vault HEAD is back at the snapshot sha; the added file is gone.
    assert _git(vault, "rev-parse", "HEAD").stdout.strip() == m["vault_sha"]
    assert not (vault / "raw" / "added.md").exists()
    # the durable was restored from the tarball.
    assert (home / "config.yaml").read_text() != "CHANGED\n"


def test_restore_refuses_dirty_tree_without_force(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _init_repo(vault)
    m = _snap.create()
    (vault / "dirty.md").write_text("uncommitted\n")   # dirty working tree

    with pytest.raises(RuntimeError):
        _snap.restore(m["ts"])
    # force overrides the guard (does not raise).
    _snap.restore(m["ts"], force=True)
