"""pytest fixtures: ephemeral workspace + per-test config + per-test DB."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A throwaway directory with gorae + workshop subspaces seeded."""
    gorae = tmp_path / "gorae"
    workshop = tmp_path / "workshop"
    (gorae / "raw" / "personal" / "diary" / "2026" / "05").mkdir(parents=True)
    (gorae / "wiki" / "entities").mkdir(parents=True)
    (gorae / "wiki" / "themes").mkdir(parents=True)
    (gorae / "wiki" / "digests").mkdir(parents=True)
    (gorae / "wiki" / "sources").mkdir(parents=True)
    (workshop / "products" / "demo").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def atelier_env(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Path]:
    """Point ~/.atelier and the DB at the temp workspace."""
    home = workspace / "atelier_home"
    cache = home / "cache"
    voices = home / "voices"
    secrets = home / "secrets"
    for d in (cache, voices, secrets):
        d.mkdir(parents=True)
    (voices / "librarian.md").write_text("# test librarian\n")
    (voices / "builder.md").write_text("# test builder\n")

    config_yaml = {
        "spaces": {
            "gorae":    {"local": str(workspace / "gorae"),
                         "remote": {"type": "github",
                                    "url": "github.com/test/gorae", "branch": "main"},
                         "role": "librarian-territory"},
            "workshop": {"local": str(workspace / "workshop"),
                         "remote": {"type": "github",
                                    "url": "github.com/test/workshop", "branch": "main"},
                         "role": "builder-territory"},
        },
        "agents": {
            "librarian": {"voice_overlay": str(voices / "librarian.md")},
            "builder":   {"voice_overlay": str(voices / "builder.md")},
        },
    }
    (home / "config.yaml").write_text(yaml.safe_dump(config_yaml))

    from runtime.util import config as _config
    monkeypatch.setattr(_config, "CONFIG_PATH", home / "config.yaml")
    monkeypatch.setattr(_config, "CACHE_DIR",   cache)
    monkeypatch.setattr(_config, "DB_PATH",     cache / "atelier.db")
    monkeypatch.setattr(_config, "VOICES_DIR",  voices)
    monkeypatch.setattr(_config, "SECRETS_ENV", secrets / ".env")

    return {"home": home, "gorae": workspace / "gorae",
            "workshop": workspace / "workshop", "cache": cache}


def write_page(path: Path, frontmatter: Dict[str, Any], body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
