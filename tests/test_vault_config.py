"""PR-5: spaces → vault config migration.

Verifies:
- `vault:` block alone synthesizes two pseudo-spaces by role
- `spaces:` block alone still works (deprecation path)
- Both blocks present is refused
- subtrees block writer values are validated
- vault.local placeholder is rejected
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest
import yaml


def _write_config(home: Path, data: Dict) -> None:
    (home / "config.yaml").write_text(yaml.safe_dump(data))


def _base_workspace(atelier_env: Dict) -> Path:
    """Return the gorae path the conftest seeded."""
    return atelier_env["gorae"]


def test_vault_block_synthesizes_role_spaces(atelier_env: Dict) -> None:
    from runtime.util import config as _config

    vault_path = _base_workspace(atelier_env)
    _write_config(atelier_env["home"], {
        "vault": {
            "local": str(vault_path),
            "remote": {"type": "github", "url": "github.com/test/vault",
                       "branch": "main"},
        },
        "subtrees": {
            "raw": {"writer": "human-only"},
            "wiki": {"writer": "librarian-write"},
            "workshop": {"writer": "builder-write"},
            "learnings/candidates": {"writer": "captor-write",
                                     "append_only": True},
            "learnings/accepted":   {"writer": "curator-write"},
            "learnings/archived":   {"writer": "curator-write"},
        },
    })

    cfg = _config.load()
    assert cfg.vault is not None
    assert cfg.vault.local == vault_path
    # space_by_role keeps working for both legacy roles, both pointing at
    # the single vault root.
    lib = cfg.space_by_role("librarian-territory")
    bldr = cfg.space_by_role("builder-territory")
    assert lib.local == vault_path
    assert bldr.local == vault_path
    # subtrees are parsed
    assert cfg.subtrees["wiki"].writer == "librarian-write"
    assert cfg.subtrees["learnings/candidates"].append_only is True


def test_legacy_spaces_block_still_works(atelier_env: Dict) -> None:
    """conftest seeded `spaces:` — load() should still accept it
    without the new vault block."""
    from runtime.util import config as _config

    cfg = _config.load()
    assert cfg.vault is None
    assert "gorae" in cfg.spaces


def test_both_blocks_present_is_refused(atelier_env: Dict) -> None:
    from runtime.util import config as _config

    vault_path = _base_workspace(atelier_env)
    cfg_path = atelier_env["home"] / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["vault"] = {
        "local": str(vault_path),
        "remote": {"type": "github", "url": "github.com/test/vault",
                   "branch": "main"},
    }
    cfg_path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="both"):
        _config.load()


def test_invalid_subtree_writer_rejected(atelier_env: Dict) -> None:
    from runtime.util import config as _config

    vault_path = _base_workspace(atelier_env)
    _write_config(atelier_env["home"], {
        "vault": {"local": str(vault_path),
                  "remote": {"type": "github", "url": "github.com/test/vault",
                             "branch": "main"}},
        "subtrees": {"wiki": {"writer": "evil-writer"}},
    })
    with pytest.raises(ValueError, match="evil-writer"):
        _config.load()


def test_vault_local_placeholder_rejected(atelier_env: Dict) -> None:
    from runtime.util import config as _config

    _write_config(atelier_env["home"], {
        "vault": {"local": "<REQUIRED — absolute path>",
                  "remote": {"type": "github", "url": "github.com/test/vault",
                             "branch": "main"}},
    })
    with pytest.raises(ValueError, match="placeholder"):
        _config.load()
