"""Filesystem helpers: hashing, safe path resolution, mtime."""
from __future__ import annotations

import hashlib
import os
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


_SKIP_DIRS = {".git", ".obsidian", "node_modules", "_attachments", ".trash"}

# Structured formats indexed alongside markdown (RFC 0002 P1b). Markdown is
# parsed as prose; the rest are flattened key:value → text and classified `data`.
# DATA_SUFFIXES is the single source of truth — `parse.is_data_path` imports it,
# so the walk and the parse dispatch can never disagree on what a data file is.
DATA_SUFFIXES = (".yaml", ".yml", ".json")
_INDEXABLE_EXT = (".md", *DATA_SUFFIXES)

# Structured files that are build/tooling artifacts, not knowledge content. A
# real vault that absorbed code repos is full of these; indexing them adds pure
# noise (package-lock.json alone is tens of thousands of generated lines). The
# exclusion is by filename (a format fact), and only applies to structured files
# — markdown is never tooling. Dot-prefixed structured files (`.eslintrc.json`,
# `.prettierrc.yaml`) are tool config by convention and skipped too.
_DATA_TOOLING_NAMES = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "bower.json",
    "composer.json", "composer.lock", "manifest.json", "deno.json",
}


def _is_tooling_data(name: str) -> bool:
    low = name.lower()
    if name.startswith("."):
        return True
    if low in _DATA_TOOLING_NAMES:
        return True
    if low.startswith("tsconfig") and low.endswith(".json"):
        return True
    if low.endswith(".eslintrc.json") or low.endswith(".prettierrc.json"):
        return True
    return False


def _excluded(rel_parts: tuple[str, ...]) -> bool:
    """Privacy/junk exclusions shared by every walk: hidden or junk dirs, any
    `secrets/` path segment, and `*.local.*` filenames (RFC 0002 §6)."""
    dirs, name = rel_parts[:-1], rel_parts[-1]
    if any(d in _SKIP_DIRS or d.startswith(".") for d in dirs):
        return True
    if "secrets" in dirs:
        return True
    if ".local." in name:
        return True
    return False


def walk_indexable(root: Path) -> Iterator[Path]:
    """Yield every indexable file under root — markdown plus structured
    `.yaml`/`.yml`/`.json` (RFC 0002 P1b) — applying the shared exclusions.

    The indexer (`crawl`) and the doctor's drift check (`D2`) MUST both use this
    one walk, or data pages the indexer writes look like phantom drift to a
    md-only scan (same single-source lesson as `reindex.canonical_spaces`)."""
    # os.walk with in-place dir pruning so we never DESCEND into skip/hidden/
    # secrets dirs (a vault that absorbed code repos has huge node_modules trees).
    # Suffix match is case-insensitive (CONFIG.YAML must index). Collect then sort
    # for deterministic, reproducible order (rm db && reindex is stable).
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(".")
                       and d != "secrets"]
        for name in filenames:
            if Path(name).suffix.lower() in _INDEXABLE_EXT:
                candidates.append(Path(dirpath) / name)
    for p in sorted(candidates):
        suffix = p.suffix.lower()
        # _excluded is redundant for dir exclusions (already pruned above) but is
        # the SOLE guard for *.local.* filenames, which dir pruning cannot catch.
        if _excluded(p.relative_to(root).parts):
            continue
        if suffix != ".md" and _is_tooling_data(p.name):
            continue
        yield p


def slug_for(root: Path, path: Path) -> str:
    """Space-relative POSIX-style slug e.g. 'wiki/entities/foo.md'."""
    return path.relative_to(root).as_posix()
