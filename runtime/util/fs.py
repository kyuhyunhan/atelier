"""Filesystem helpers: hashing, safe path resolution, mtime."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterator


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_markdown(root: Path) -> Iterator[Path]:
    """Yield all .md files under root, ignoring hidden dirs and common junk."""
    SKIP = {".git", ".obsidian", "node_modules", "_attachments", ".trash"}
    for p in root.rglob("*.md"):
        if any(part in SKIP or part.startswith(".") for part in p.relative_to(root).parts[:-1]):
            continue
        yield p


def slug_for(root: Path, path: Path) -> str:
    """Space-relative POSIX-style slug e.g. 'wiki/entities/foo.md'."""
    return path.relative_to(root).as_posix()
