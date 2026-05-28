"""PR-6: schema v3 → v4 migration script.

Verifies:
- dry-run prints summary, writes nothing
- apply rewrites frontmatter (schema_version, entry_id PENDING)
- idempotent re-run is a no-op without --force
- already-v4 files are skipped
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest
import yaml

from scripts.migrate_schema_v3_to_v4 import migrate as _mig


def _seed_v3_file(root: Path, rel: str, *, entry_id: str = "PENDING") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "schema_version": 3,
        "entry_id": entry_id,
        "title": "old",
        "type": "diary",
    }
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    p.write_text(f"---\n{serialized}\n---\nbody here\n", encoding="utf-8")
    return p


def _seed_v4_file(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = {"schema_version": 4, "entry_id": "abc", "title": "new", "type": "diary"}
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    p.write_text(f"---\n{serialized}\n---\nbody\n", encoding="utf-8")
    return p


def test_dry_run_makes_no_changes(atelier_env: Dict, capsys) -> None:
    gorae = atelier_env["gorae"]
    p = _seed_v3_file(gorae, "raw/personal/diary/2026/05/old.md")
    before = p.read_text()

    rc = _mig.migrate(role="librarian-territory", apply=False, force=False)
    assert rc == 0
    after = p.read_text()
    assert before == after
    out = capsys.readouterr().out
    assert "would-change: 1" in out
    assert "dry-run" in out


def test_apply_rewrites_frontmatter(atelier_env: Dict) -> None:
    gorae = atelier_env["gorae"]
    p = _seed_v3_file(gorae, "raw/personal/diary/2026/05/old.md")

    rc = _mig.migrate(role="librarian-territory", apply=True, force=False)
    assert rc == 0

    from runtime.index.parse import split_frontmatter
    fm, body = split_frontmatter(p.read_text())
    assert fm["schema_version"] == 4
    assert fm["entry_id"] != "PENDING"
    assert fm["title"] == "old"  # preserved
    assert "body here" in body


def test_idempotent_second_run(atelier_env: Dict, capsys) -> None:
    gorae = atelier_env["gorae"]
    _seed_v3_file(gorae, "raw/personal/diary/2026/05/old.md")
    assert _mig.migrate(role="librarian-territory", apply=True, force=False) == 0
    capsys.readouterr()  # drain
    rc = _mig.migrate(role="librarian-territory", apply=True, force=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "already migrated" in out


def test_already_v4_files_are_skipped(atelier_env: Dict, capsys) -> None:
    gorae = atelier_env["gorae"]
    _seed_v4_file(gorae, "wiki/entities/foo.md")
    _seed_v3_file(gorae, "raw/personal/diary/2026/05/old.md")

    rc = _mig.migrate(role="librarian-territory", apply=False, force=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "would-change: 1" in out
    assert "already-v4:   1" in out
