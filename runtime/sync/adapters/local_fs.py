"""Local-FS sync — for testing and offline operation."""
from __future__ import annotations

import shutil
from pathlib import Path


def mirror(src: Path, dst: Path, dry_run: bool = False) -> int:
    """Copy any file that differs from src to dst. Returns count of files touched."""
    n = 0
    for s in src.rglob("*"):
        if s.is_dir():
            continue
        rel = s.relative_to(src)
        d = dst / rel
        if d.exists() and d.read_bytes() == s.read_bytes():
            continue
        if not dry_run:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
        n += 1
    return n
