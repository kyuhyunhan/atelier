"""Schema v3 → v4 one-shot migration.

Walks every `*.md` file under one configured space (resolved by role),
updates the frontmatter to v4 (raising `schema_version` and resolving
`entry_id: PENDING` to a stable UUID), and records a meta marker in the
SQLite cache so reruns are no-ops.

Usage:
    python -m scripts.migrate_schema_v3_to_v4.migrate \\
        --role librarian-territory \\
        [--dry-run | --apply] [--force]

By default the script runs `--dry-run` and prints a summary diff. Apply
must be requested explicitly. The script never commits; the operator
reviews the diff in the content repo and commits there.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import uuid as _uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from runtime.index import parse as _parse
from runtime.util import config as _config
from runtime.util import db as _db


META_KEY = "schema_migration_v3_to_v4"
TARGET_VERSION = 4


def _git_porcelain(path: Path) -> str:
    """Return `git status --porcelain` output; empty string when clean."""
    if not (path / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True, capture_output=True, text=True,
        )
        return out.stdout
    except subprocess.CalledProcessError as e:  # pragma: no cover
        return e.stderr or "(git status failed)"


def _iter_markdown(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        yield p


def _resolve_entry_id(fm: Dict[str, Any], rel_path: str) -> str:
    """Stable UUID5 derived from the file's vault-relative path."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"atelier:{rel_path}"))


def _bump_one(path: Path, root: Path) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """Return (would_change, reason, new_frontmatter). reason='already-v4'
    means the file is at target; would_change is False."""
    text = path.read_text(encoding="utf-8")
    fm, body = _parse.split_frontmatter(text)

    current = fm.get("schema_version")
    if current == TARGET_VERSION:
        return False, "already-v4", fm

    new_fm = dict(fm)
    new_fm["schema_version"] = TARGET_VERSION

    if str(new_fm.get("entry_id", "")).strip().upper() == "PENDING" or "entry_id" not in new_fm:
        rel = str(path.relative_to(root))
        new_fm["entry_id"] = _resolve_entry_id(new_fm, rel)

    return True, None, new_fm


def _write_fm(path: Path, new_fm: Dict[str, Any]) -> None:
    text = path.read_text(encoding="utf-8")
    _, body = _parse.split_frontmatter(text)
    serialized = yaml.safe_dump(new_fm, allow_unicode=True, sort_keys=False).rstrip()
    path.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")


def migrate(role: str, *, apply: bool, force: bool) -> int:
    cfg = _config.load()
    space = cfg.space_by_role(role)
    root = space.local

    if not root.exists():
        print(f"ERROR: space root does not exist: {root}", file=sys.stderr)
        return 2

    # Idempotency check via meta marker.
    conn = _db.connect()
    try:
        prior = _db.get_meta(conn, META_KEY)
    finally:
        conn.close()
    if prior and not force:
        print(f"already migrated at {prior}; pass --force to re-run")
        return 0

    porcelain = _git_porcelain(root)
    if porcelain and apply:
        print(f"ERROR: {root} has uncommitted changes; commit or stash first.\n{porcelain}",
              file=sys.stderr)
        return 2

    by_dir: Counter[str] = Counter()
    skipped_v4: List[Path] = []
    changes: List[Path] = []

    for p in _iter_markdown(root):
        would, reason, new_fm = _bump_one(p, root)
        if not would and reason == "already-v4":
            skipped_v4.append(p)
            continue
        changes.append(p)
        bucket = "/".join(p.relative_to(root).parts[:2]) or "(root)"
        by_dir[bucket] += 1
        if apply:
            _write_fm(p, new_fm)

    print(f"vault: {root}")
    print(f"would-change: {len(changes)} files")
    print(f"already-v4:   {len(skipped_v4)} files")
    if by_dir:
        print("by directory:")
        for bucket, n in sorted(by_dir.items()):
            print(f"  {n:5}  {bucket}")

    if apply and changes:
        conn = _db.connect()
        try:
            _db.set_meta(conn, META_KEY,
                         datetime.now(timezone.utc).isoformat(timespec="seconds"))
            conn.commit()
        finally:
            conn.close()
        print(f"\napplied. operator must commit the {len(changes)} changes in {root}.")
    elif apply and not changes:
        print("nothing to apply.")
    else:
        print("\n(dry-run; pass --apply to write changes)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="migrate-schema-v3-to-v4",
        description="One-shot frontmatter migration from v3 to v4.",
    )
    p.add_argument("--role", required=True,
                   help="space role to migrate (e.g. librarian-territory)")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true", default=True,
                     help="report only (default)")
    grp.add_argument("--apply", action="store_true",
                     help="actually write changes (still does not git-commit)")
    p.add_argument("--force", action="store_true",
                   help="re-run even if meta marker says already migrated")
    args = p.parse_args(argv)
    return migrate(role=args.role, apply=args.apply, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
