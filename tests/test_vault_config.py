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

import pytest
import yaml


def _write_config(home: Path, data: dict) -> None:
    (home / "config.yaml").write_text(yaml.safe_dump(data))


def _base_workspace(atelier_env: dict) -> Path:
    """Return the wiki path the conftest seeded."""
    return atelier_env["wiki"]


def test_vault_block_synthesizes_role_spaces(atelier_env: dict) -> None:
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


def test_legacy_spaces_block_still_works(atelier_env: dict) -> None:
    """conftest seeded `spaces:` — load() should still accept it
    without the new vault block."""
    from runtime.util import config as _config

    cfg = _config.load()
    assert cfg.vault is None
    assert "wiki" in cfg.spaces


def test_both_blocks_present_is_refused(atelier_env: dict) -> None:
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


def test_invalid_subtree_writer_rejected(atelier_env: dict) -> None:
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


def test_vault_local_placeholder_rejected(atelier_env: dict) -> None:
    from runtime.util import config as _config

    _write_config(atelier_env["home"], {
        "vault": {"local": "<REQUIRED — absolute path>",
                  "remote": {"type": "github", "url": "github.com/test/vault",
                             "branch": "main"}},
    })
    with pytest.raises(ValueError, match="placeholder"):
        _config.load()


# ── the ONE vault-root accessor (RFC 0001 §6, closed via issue #98) ──────────

def test_vault_root_prefers_the_vault_block(atelier_env: dict) -> None:
    """Single-vault config: `vault_root()` is `vault.local` — the accessor the
    22 per-module `_vault_root()` helpers duplicated before the collapse."""
    from runtime.util import config as _config
    vault_path = _base_workspace(atelier_env)
    _write_config(atelier_env["home"], {
        "vault": {"local": str(vault_path),
                  "remote": {"type": "github", "url": "github.com/test/vault",
                             "branch": "main"}},
        "subtrees": {"raw": {"writer": "human-only"}},
    })
    cfg = _config.load()
    assert cfg.vault_root() == vault_path
    assert _config.vault_root() == vault_path        # module-level convenience


def test_vault_root_falls_back_to_the_librarian_space(atelier_env: dict) -> None:
    """Legacy two-space config: the fallback is the librarian-territory space's
    local root — byte-identical to what every duplicated helper computed."""
    from runtime.util import config as _config
    ws = atelier_env["home"] / "ws"
    (ws / "wiki").mkdir(parents=True)
    (ws / "workshop").mkdir(parents=True)
    _write_config(atelier_env["home"], {
        "spaces": {
            "wiki": {"role": "librarian-territory", "local": str(ws / "wiki"),
                      "remote": {"type": "github", "url": "github.com/t/g",
                                 "branch": "main"}},
            "workshop": {"role": "builder-territory", "local": str(ws / "workshop"),
                         "remote": {"type": "github", "url": "github.com/t/w",
                                    "branch": "main"}},
        },
    })
    cfg = _config.load()
    assert cfg.vault_root() == ws / "wiki"
    assert _config.vault_root() == ws / "wiki"      # module entry point too


def test_asset_dir_default_and_pin(vault_env, monkeypatch):
    """`vault.assets.dir` — neutral default, per-machine pin (PR #107)."""
    import yaml

    from runtime.util import config as _config
    cfg_path = vault_env["home"] / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    assert _config.load(cfg_path).asset_dir() == "assets"          # default
    raw["vault"]["assets"] = {"dir": "historic-resources"}
    cfg_path.write_text(yaml.safe_dump(raw))
    assert _config.load(cfg_path).asset_dir() == "historic-resources"


def test_asset_dir_legacy_spaces_shape(atelier_env):
    """The legacy `spaces:` shape pins through the librarian space's assets
    block — without the fallback a legacy machine silently loses the knob."""
    import yaml

    from runtime.util import config as _config
    cfg_path = atelier_env["home"] / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    assert _config.load(cfg_path).asset_dir() == "assets"
    raw["spaces"]["wiki"]["assets"] = {"dir": "historic-resources"}
    cfg_path.write_text(yaml.safe_dump(raw))
    assert _config.load(cfg_path).asset_dir() == "historic-resources"
