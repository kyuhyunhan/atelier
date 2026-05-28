"""GitHub sync via shell `git`. No gitpython dep."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class GitStatus:
    space: str
    clean: bool
    ahead: int
    behind: int
    unstaged: List[str]
    untracked: List[str]


def _git(local: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(local), *args],
        stderr=subprocess.STDOUT, text=True,
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


def pull(local: Path) -> str:
    return _git(local, "pull", "--ff-only")


def push(local: Path) -> str:
    return _git(local, "push")
