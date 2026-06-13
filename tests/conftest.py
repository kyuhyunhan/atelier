"""pytest fixtures: ephemeral workspace + per-test config + per-test DB."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

# Kill switch for the reindex embed pass (RFC 0002 P2). A dev machine often has
# a live Ollama — without this, every fixture reindex would silently embed tiny
# test vaults through the real provider (slow, non-deterministic). Set at import
# (not per-fixture) so subprocesses spawned by serve/MCP tests inherit it. Tests
# that exercise embedding pass a fake gateway explicitly via reindex_space.
os.environ.setdefault("ATELIER_EMBED", "off")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A throwaway directory with gorae + workshop subspaces seeded."""
    gorae = tmp_path / "gorae"
    workshop = tmp_path / "workshop"
    # Canonical post-RFC-0003 structure (provenance/ + graph/). Tests that
    # specifically exercise the legacy wiki//raw//learnings/ trees or the rename
    # aliasing create those dirs themselves.
    (gorae / "provenance" / "personal" / "diary" / "2026" / "05").mkdir(parents=True)
    (gorae / "provenance" / "knowledge").mkdir(parents=True)
    (gorae / "graph" / "entities").mkdir(parents=True)
    (gorae / "graph" / "sources").mkdir(parents=True)
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


@pytest.fixture
def vault_env(atelier_env: Dict[str, Path], monkeypatch: pytest.MonkeyPatch
              ) -> Dict[str, Path]:
    """Single-vault (`vault:` + `subtrees:`) config over one directory — the
    production v0.2 shape. Reuses atelier_env's home/cache/monkeypatching and
    overwrites config.yaml with a vault block."""
    home = atelier_env["home"]
    vault = atelier_env["gorae"].parent / "vault"
    # Note: provenance/learning/notes is intentionally NOT pre-seeded — the flat
    # accepted store is created on demand by writers; pre-creating it would break
    # "nothing moved" dry-run assertions (mirrors the pre-migration fixture).
    for sub in ("provenance/personal", "provenance/knowledge",
                "provenance/learning/candidates",
                "provenance/learning/principles", "provenance/learning/archived",
                "graph/entities", "graph/sources", "workshop/products"):
        (vault / sub).mkdir(parents=True, exist_ok=True)

    (home / "config.yaml").write_text(yaml.safe_dump({
        "vault": {"local": str(vault),
                  "remote": {"type": "github",
                             "url": "github.com/test/vault", "branch": "main"}},
        "subtrees": {
            "provenance": {"writer": "human-only"},
            "graph": {"writer": "librarian-write"},
            "workshop": {"writer": "builder-write"},
            # candidates is append-only/captor-write; the broader provenance/learning
            # key covers notes/principles/archived under curator-write.
            "provenance/learning/candidates": {"writer": "captor-write", "append_only": True},
            "provenance/learning":            {"writer": "curator-write"},
        },
    }))
    return {"home": home, "vault": vault, "cache": atelier_env["cache"]}


def write_page(path: Path, frontmatter: Dict[str, Any], body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
