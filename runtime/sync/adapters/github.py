"""GitHub sync via shell `git`. No gitpython dep."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class GitStatus:
    space: str
    clean: bool
    ahead: int
    behind: int
    unstaged: List[str]
    untracked: List[str]


_DEFAULT_TIMEOUT = 30  # seconds; git calls must never hang the caller


def _git(local: Path, *args: str, timeout: Optional[float] = _DEFAULT_TIMEOUT) -> str:
    return subprocess.check_output(
        ["git", "-C", str(local), *args],
        stderr=subprocess.STDOUT, text=True, timeout=timeout,
    )


def status(space_name: str, local: Path) -> GitStatus:
    porcelain = _git(local, "status", "--porcelain=v2", "--branch")
    ahead = behind = 0
    unstaged: List[str] = []
    untracked: List[str] = []
    for line in porcelain.splitlines():
        if line.startswith("# branch.ab"):
            parts = line.split()
            ahead = int(parts[2].lstrip("+"))
            behind = int(parts[3].lstrip("-"))
        elif line.startswith("1 ") or line.startswith("2 "):
            unstaged.append(line.split(" ", 8)[-1])
        elif line.startswith("? "):
            untracked.append(line.split(" ", 1)[-1])
    clean = not unstaged and not untracked
    return GitStatus(space_name, clean, ahead, behind, unstaged, untracked)


def pull(local: Path, timeout: Optional[float] = _DEFAULT_TIMEOUT) -> str:
    return _git(local, "pull", "--ff-only", timeout=timeout)


def push(local: Path, timeout: Optional[float] = _DEFAULT_TIMEOUT) -> str:
    return _git(local, "push", timeout=timeout)


# ── commit primitive + safety predicates (PR: vault auto-sync) ───────────────


def commit(local: Path, message: str,
           timeout: Optional[float] = _DEFAULT_TIMEOUT) -> str:
    """Stage everything under the repo and commit — but only if something is
    actually staged. Returns the new commit sha, or the literal
    ``"nothing to commit"`` when the tree was clean (idempotent no-op).

    Uses ``add -A -- .`` so staging is scoped to the repo at *local*; callers
    must still verify *local* is the repo toplevel (see ``is_repo_root``).
    """
    _git(local, "add", "-A", "--", ".", timeout=timeout)
    staged = _git(local, "diff", "--cached", "--name-only", timeout=timeout).strip()
    if not staged:
        return "nothing to commit"
    _git(local, "commit", "-m", message, timeout=timeout)
    return _git(local, "rev-parse", "HEAD", timeout=timeout).strip()


def commit_split(local: Path, human_tree: str, *,
                 human_prefix: str = "journal:",
                 machine_prefix: str = "chore(vault):",
                 timeout: Optional[float] = _DEFAULT_TIMEOUT) -> List[str]:
    """Two path-scoped commits instead of one ``add -A``: the HUMAN tree
    (``<human_tree>/`` — e.g. raw/, the content root) first, then everything
    else (graph/, workshop/, manifests — the engine/machine tree).

    Why: the vault interleaves human diary edits and machine claim writes; one
    ``add -A`` commit fuses them, polluting the journal's git history and the
    PII-review surface (you cannot diff what the machine extracted without also
    diffing the diary). Splitting restores "raw/ is MINE, graph/ is the
    MACHINE's" at the history level — same repo, same durability, zero new
    machinery. Each commit's message is built from ITS OWN staged paths.

    Skips either commit when its tree is clean (idempotent, like ``commit``).
    Returns the new shas, oldest first (0, 1, or 2 entries).
    """
    shas: List[str] = []
    passes = (
        (human_prefix, [f"{human_tree.rstrip('/')}/"]),
        (machine_prefix, ["."]),        # add -A of the remainder after pass 1
    )
    for prefix, pathspecs in passes:
        _git(local, "add", "-A", "--", *pathspecs, timeout=timeout)
        staged = _git(local, "diff", "--cached", "--name-only",
                      timeout=timeout).strip()
        if not staged:
            continue
        files = staged.splitlines()
        msg = f"{prefix} sync {len(files)} change(s) [auto]\n\n" + "\n".join(files)
        _git(local, "commit", "-m", msg, timeout=timeout)
        shas.append(_git(local, "rev-parse", "HEAD", timeout=timeout).strip())
    return shas


def dirty_porcelain(local: Path,
                    timeout: Optional[float] = _DEFAULT_TIMEOUT) -> str:
    """Raw ``git status --porcelain`` output (empty string == clean tree).

    Used as a quiescence fingerprint: the poller commits only once this
    string stops changing between ticks."""
    return _git(local, "status", "--porcelain", timeout=timeout).strip()


def is_repo_root(local: Path) -> bool:
    """True iff *local* is the toplevel of a git work tree. Guards against a
    repo-wide ``add -A`` when the vault path is nested inside a larger repo."""
    try:
        top = _git(local, "rev-parse", "--show-toplevel", timeout=5).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    try:
        return Path(top).resolve() == Path(local).resolve()
    except OSError:
        return False


def _git_dir(local: Path) -> Optional[Path]:
    try:
        out = _git(local, "rev-parse", "--git-dir", timeout=5).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    p = Path(out)
    return p if p.is_absolute() else (Path(local) / p)


def in_merge_or_rebase(local: Path) -> bool:
    """True if the repo is mid merge / rebase / cherry-pick / revert. Committing
    during any of these would clobber a human's in-progress operation."""
    gd = _git_dir(local)
    if gd is None:
        return False
    markers = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
               "rebase-merge", "rebase-apply")
    return any((gd / m).exists() for m in markers)


def lock_present(local: Path) -> bool:
    """True if another git process holds the index lock."""
    gd = _git_dir(local)
    if gd is None:
        return False
    return (gd / "index.lock").exists()
