"""DB → markdown writeback. Used by L3/L4 fixers and promote apply.

Stable serialization: only the affected frontmatter keys are updated.
Body and other frontmatter keys are preserved byte-for-byte where possible.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import yaml

from .parse import split_frontmatter


def patch_frontmatter(path: Path, updates: Dict[str, Any]) -> bool:
    """Apply updates to the frontmatter at `path`. Returns True if file changed."""
    text = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    changed = False
    for k, v in updates.items():
        if fm.get(k) != v:
            fm[k] = v
            changed = True
    if not changed:
        return False
    new_fm = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).rstrip()
    new_text = f"---\n{new_fm}\n---\n{body}"
    path.write_text(new_text, encoding="utf-8")
    return True
