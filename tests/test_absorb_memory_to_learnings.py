"""PR-21: absorb workshop memory → learnings/by-{topic,project}/."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest
import yaml

from scripts.absorb_workshop_memory_to_learnings import absorb as _ab


def _seed_workshop(workshop_root: Path) -> None:
    """Create vault/workshop/products/<product>/memory/<topic>/<file>.md
    fixtures."""
    products = workshop_root / "products"
    base = products / "lexio" / "memory"
    (base / "cross-cutting").mkdir(parents=True)
    (base / "cross-cutting" / "release.md").write_text(
        "---\nschema_version: 3\ntitle: release notes\n---\n"
        "## Observation\nrelease cuts the wrong sha\n"
        "## Why this matters\nshipped wrong build\n",
        encoding="utf-8",
    )
    (base / "client").mkdir()
    (base / "client" / "ui.md").write_text(
        "---\nschema_version: 3\n---\nclient ui issue\n",
        encoding="utf-8",
    )


def test_dry_run_no_writes(atelier_env: Dict, capsys) -> None:
    # Seed workshop INSIDE the vault (post-PR-7 layout).
    _seed_workshop(atelier_env["gorae"] / "workshop")
    rc = _ab.absorb(apply=False)
    assert rc == 0
    learnings = atelier_env["gorae"] / "learnings"
    assert not learnings.exists()
    out = capsys.readouterr().out
    assert "plans:" in out
    assert "dry-run" in out


def test_apply_creates_by_topic_and_by_project(atelier_env: Dict) -> None:
    _seed_workshop(atelier_env["gorae"] / "workshop")
    rc = _ab.absorb(apply=True)
    assert rc == 0
    vault = atelier_env["gorae"]
    by_topic = vault / "learnings" / "accepted" / "by-topic"
    by_proj  = vault / "learnings" / "accepted" / "by-project"

    assert (by_topic / "cross-cutting" / "release.md").exists()
    assert (by_proj / "lexio" / "release.md").exists()
    assert (by_topic / "client" / "ui.md").exists()
    assert (by_proj / "lexio" / "ui.md").exists()

    from runtime.index.parse import split_frontmatter
    fm, body = split_frontmatter(
        (by_topic / "cross-cutting" / "release.md").read_text(encoding="utf-8")
    )
    assert fm["schema_version"] == 4
    assert fm["status"] == "accepted"
    assert fm["ac_status"] == "passed"
    assert fm["target_topic"] == "cross-cutting"
    assert fm["target_project"] == "lexio"
    assert "release cuts the wrong sha" in body


def test_apply_conflict_blocks(atelier_env: Dict) -> None:
    _seed_workshop(atelier_env["gorae"] / "workshop")
    pre = (atelier_env["gorae"] / "learnings" / "accepted" / "by-topic"
           / "cross-cutting" / "release.md")
    pre.parent.mkdir(parents=True)
    pre.write_text("---\nschema_version: 4\n---\npre-existing\n")
    rc = _ab.absorb(apply=True)
    assert rc == 2
    assert "pre-existing" in pre.read_text()


def test_missing_workshop_handled(atelier_env: Dict, tmp_path: Path) -> None:
    # No workshop seeded → script should run, report 0 plans, exit 0.
    rc = _ab.absorb(apply=False)
    assert rc == 0
