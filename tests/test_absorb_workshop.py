"""PR-7: absorb_workshop migration script.

Verifies:
- dry-run prints plan, writes nothing
- apply copies products/, notes/, consolidates logs/, extracts profiles
- conflict (dest exists) is detected and reported; apply refuses
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from scripts.absorb_workshop import absorb as _ab


def _seed_builder(workshop_root: Path) -> None:
    products = workshop_root / "products"
    (products / "lexio").mkdir(parents=True)
    (products / "lexio" / "README.md").write_text("# lexio\n")
    (products / "lexio" / "profile.local.yaml").write_text("workdir: /tmp/lexio\n")
    (products / "lexio" / "spec").mkdir()
    (products / "lexio" / "spec" / "intro.md").write_text("intro\n")

    (workshop_root / "notes").mkdir()
    (workshop_root / "notes" / "weekly.md").write_text("weekly note\n")

    (workshop_root / "logs").mkdir()
    (workshop_root / "logs" / "2026-05-01.md").write_text("first log\n")
    (workshop_root / "logs" / "2026-05-02.md").write_text("second log\n")


def test_dry_run_does_not_write(atelier_env: Dict, capsys, tmp_path: Path) -> None:
    _seed_builder(atelier_env["workshop"])
    profiles_dir = tmp_path / "profiles"

    rc = _ab.absorb(apply=False, profiles_dir=profiles_dir)
    assert rc == 0
    out = capsys.readouterr().out
    assert "would do" in out
    # Nothing actually copied:
    assert not (atelier_env["gorae"] / "workshop").exists()
    assert not profiles_dir.exists()


def test_apply_copies_products_and_notes(atelier_env: Dict, tmp_path: Path) -> None:
    _seed_builder(atelier_env["workshop"])
    profiles_dir = tmp_path / "profiles"

    rc = _ab.absorb(apply=True, profiles_dir=profiles_dir)
    assert rc == 0

    workshop = atelier_env["gorae"] / "workshop"
    assert (workshop / "products" / "lexio" / "README.md").read_text() == "# lexio\n"
    assert (workshop / "products" / "lexio" / "spec" / "intro.md").read_text() == "intro\n"
    # profile.local.yaml extracted out of the vault:
    assert (profiles_dir / "lexio.yaml").read_text() == "workdir: /tmp/lexio\n"
    assert (profiles_dir / "lexio.yaml").exists()
    # notes/ moved:
    assert (workshop / "notes" / "weekly.md").read_text() == "weekly note\n"
    # logs/ consolidated:
    consolidated = (workshop / "log.md").read_text()
    assert "first log" in consolidated and "second log" in consolidated
    assert "## 2026-05-01.md" in consolidated


def test_conflict_blocks_apply(atelier_env: Dict, tmp_path: Path) -> None:
    """If gorae/workshop/products/lexio already exists, --apply must refuse."""
    _seed_builder(atelier_env["workshop"])
    pre = atelier_env["gorae"] / "workshop" / "products" / "lexio"
    pre.mkdir(parents=True)
    (pre / "README.md").write_text("pre-existing\n")

    rc = _ab.absorb(apply=True, profiles_dir=tmp_path / "profiles")
    assert rc == 2  # refusal on conflicts
    # Pre-existing file untouched:
    assert (pre / "README.md").read_text() == "pre-existing\n"


def test_missing_builder_root_handled(atelier_env: Dict, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    # Move builder root away — script should refuse with rc=2.
    builder = atelier_env["workshop"]
    import shutil as _sh
    _sh.rmtree(builder)
    rc = _ab.absorb(apply=False, profiles_dir=tmp_path / "profiles")
    assert rc == 2
