"""Data-safety snapshot — the ROLLBACK artifact for RFC 0006's program (§5).

This is deliberately NOT the verification baseline (that is
`learnings/baseline.py`, a diffed comparison artifact). A snapshot is *state* you
restore, never diff: before a pillar mutates the vault, freeze a known-good point
so a bad change is fully reversible.

Two halves, because the memory system's durable state lives in two places:
- the **vault** (git-tracked markdown = truth) → a lightweight `git tag` + the
  recorded HEAD sha;
- the untracked **`~/.atelier/` durables** (`config.yaml`, `voices/`, `secrets/`,
  `pii_patterns.txt`) → a `tar.gz`, since git does not cover them.

Both, plus a manifest, land under `~/.atelier/snapshots/<ts>/`. `create()` is
strictly additive (a tag + a tarball — it never mutates the vault). `restore()`
is the only destructive path and is guarded: it refuses a dirty vault tree unless
`force=True`, so it can never silently discard uncommitted work.
"""
from __future__ import annotations

import json
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util import config as _config
from ..util import logging as _log

# Untracked durables under the atelier home, relative to it. Missing ones are
# skipped (a fresh install may lack secrets/ or pii_patterns.txt).
_DURABLE_RELPATHS = ("config.yaml", "voices", "secrets", "pii_patterns.txt")
_TAG_PREFIX = "atelier-snapshot-"


def _vault_root() -> Path:
    cfg = _config.load()
    if getattr(cfg, "vault", None) is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _atelier_home() -> Path:
    """The atelier home dir (`~/.atelier`, or the test-monkeypatched location).
    Derived from CONFIG_PATH so tests that repoint config also repoint this."""
    return _config.CONFIG_PATH.parent


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args],
                          capture_output=True, text=True)


def _is_git_repo(vault: Path) -> bool:
    r = _git(vault, "rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def _tree_dirty(vault: Path) -> bool:
    r = _git(vault, "status", "--porcelain")
    return bool(r.stdout.strip())


def _timestamp() -> str:
    # Compact, sortable, filesystem-safe: 20260704T073000Z
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _snapshots_dir() -> Path:
    return _atelier_home() / "snapshots"


def create() -> Dict[str, Any]:
    """Freeze a rollback point. Additive only — never mutates the vault.

    Tags the vault at HEAD (when it is a git repo) and tars the `~/.atelier`
    durables. Returns the manifest (also written to disk)."""
    vault = _vault_root()
    home = _atelier_home()
    ts = _timestamp()
    dest = _snapshots_dir() / ts
    dest.mkdir(parents=True, exist_ok=True)

    tag: Optional[str] = None
    vault_sha: Optional[str] = None
    if _is_git_repo(vault):
        head = _git(vault, "rev-parse", "HEAD")
        vault_sha = head.stdout.strip() or None
        tag = f"{_TAG_PREFIX}{ts}"
        r = _git(vault, "tag", tag)
        if r.returncode != 0:                    # pragma: no cover - defensive
            _log.warn("snapshot.tag-failed", vault=str(vault), tag=tag,
                      error=r.stderr.strip())
            tag = None
    else:
        _log.warn("snapshot.vault-not-git", vault=str(vault),
                  hint="vault has no git history; only durables are captured")

    # Tar the durables (relative to home so restore untars back over home).
    tar_path = dest / "durables.tar.gz"
    captured: List[str] = []
    with tarfile.open(tar_path, "w:gz") as tar:
        for rel in _DURABLE_RELPATHS:
            src = home / rel
            if src.exists():
                tar.add(src, arcname=rel)
                captured.append(rel)

    manifest = {
        "ts": ts,
        "vault": str(vault),
        "vault_sha": vault_sha,
        "tag": tag,
        "durables": captured,
        "durables_tar": str(tar_path),
        "restore_hint": (
            f"atelier snapshot restore {ts}   "
            "# git reset --hard <tag> + untar durables (guarded: refuses a dirty "
            "vault tree without --force)"
        ),
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    _log.info("snapshot.created", ts=ts, tag=tag or "(none)",
              durables=len(captured))
    return manifest


def list_snapshots() -> List[Dict[str, Any]]:
    """All snapshots, newest first, read from their manifests."""
    root = _snapshots_dir()
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for d in sorted(root.iterdir(), reverse=True):
        mf = d / "manifest.json"
        if mf.is_file():
            try:
                out.append(json.loads(mf.read_text(encoding="utf-8")))
            except Exception:                    # pragma: no cover - tolerant
                continue
    return out


def restore(snapshot_id: str, *, force: bool = False) -> Dict[str, Any]:
    """Roll the vault + durables back to a snapshot. DESTRUCTIVE.

    Guard: refuses a dirty vault tree unless `force=True`, so uncommitted work is
    never silently discarded. Returns a summary of what was restored."""
    dest = _snapshots_dir() / snapshot_id
    mf = dest / "manifest.json"
    if not mf.is_file():
        raise FileNotFoundError(f"no snapshot {snapshot_id!r} at {dest}")
    manifest = json.loads(mf.read_text(encoding="utf-8"))

    vault = Path(manifest["vault"])
    tag = manifest.get("tag")
    restored_vault = False
    if tag and _is_git_repo(vault):
        if _tree_dirty(vault) and not force:
            raise RuntimeError(
                f"vault tree at {vault} has uncommitted changes; refusing to "
                f"reset --hard to {tag}. Commit/stash first, or pass force=True.")
        r = _git(vault, "reset", "--hard", tag)
        if r.returncode != 0:                    # pragma: no cover - defensive
            raise RuntimeError(f"git reset --hard {tag} failed: {r.stderr.strip()}")
        restored_vault = True

    # Untar durables back over the atelier home.
    home = _atelier_home()
    tar_path = Path(manifest.get("durables_tar") or (dest / "durables.tar.gz"))
    restored_durables: List[str] = []
    if tar_path.is_file():
        with tarfile.open(tar_path, "r:gz") as tar:
            # `filter="data"` (3.12+) blocks path traversal / unsafe members; we
            # author these tarballs with home-relative names, but restore should
            # never trust a file on disk blindly. Fall back on older Python.
            try:
                tar.extractall(home, filter="data")   # type: ignore[call-arg]
            except TypeError:                          # pragma: no cover - <3.12
                tar.extractall(home)
            restored_durables = [m.name for m in tar.getmembers() if m.isfile()]

    _log.info("snapshot.restored", ts=snapshot_id, vault=restored_vault,
              durables=len(restored_durables))
    return {
        "ts": snapshot_id,
        "vault_restored": restored_vault,
        "tag": tag,
        "durables_restored": restored_durables,
    }
