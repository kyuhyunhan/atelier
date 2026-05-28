"""PR-9: fix PENDING entry_ids across the vault.

Walks raw/ (and any other subtree) for files with `entry_id: PENDING`
and rewrites them to a stable UUID5 derived from the file's
vault-relative path. Mirrors the schema-migration script's algorithm so
the two stay consistent.
"""
from __future__ import annotations

import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ...index import parse as _parse
from ...util import config as _config


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _new_uuid(rel: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"atelier:{rel}"))


def fix_pending(*, dry_run: bool = False,
                role: str = "librarian-territory") -> Dict[str, Any]:
    root = _vault_root()
    fixed: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []

    for md in sorted(root.rglob("*.md")):
        rel = md.resolve().relative_to(root.resolve()).as_posix()
        text = md.read_text(encoding="utf-8")
        fm, body = _parse.split_frontmatter(text)
        if str(fm.get("entry_id", "")).strip().upper() != "PENDING":
            continue
        new_id = _new_uuid(rel)
        fixed.append({"path": str(md), "rel": rel, "new_entry_id": new_id})
        if dry_run:
            continue
        fm = dict(fm)
        fm["entry_id"] = new_id
        serialized = yaml.safe_dump(fm, sort_keys=False,
                                    allow_unicode=True).rstrip()
        md.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")

    return {
        "vault": str(root),
        "fixed": fixed,
        "skipped": skipped,
        "dry_run": dry_run,
        "count": len(fixed),
    }
