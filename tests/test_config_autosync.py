"""Phase 2 — AutoSyncConfig parsing from the `vault.auto_commit` block."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from runtime.util import config as _config


def _load_with_autocommit(vault_env: Dict[str, Path], block: Any) -> _config.Config:
    home = vault_env["home"]
    data = yaml.safe_load((home / "config.yaml").read_text())
    if block is not None:
        data["vault"]["auto_commit"] = block
    (home / "config.yaml").write_text(yaml.safe_dump(data))
    return _config.load(home / "config.yaml")


def test_autosync_defaults_when_no_block(vault_env) -> None:
    cfg = _load_with_autocommit(vault_env, None)
    a = cfg.auto_sync
    assert a.enabled is False               # opt-in
    assert a.interval_seconds == 30
    assert a.push is True
    assert a.on_conflict == "surface"
    assert a.require_stable is True
    assert a.message_prefix == "chore(vault):"
    assert a.reindex_on_commit is True       # RFC 0005 §7.2 — on by default


def test_autosync_parses_explicit_values(vault_env) -> None:
    cfg = _load_with_autocommit(vault_env, {
        "enabled": True,
        "interval_seconds": 15,
        "push": False,
        "on_conflict": "surface",
        "require_stable": False,
        "message_prefix": "data(vault):",
        "reindex_on_commit": False,
    })
    a = cfg.auto_sync
    assert a.enabled is True
    assert a.interval_seconds == 15
    assert a.push is False
    assert a.require_stable is False
    assert a.message_prefix == "data(vault):"
    assert a.reindex_on_commit is False      # explicitly opted out


def test_autosync_partial_block_keeps_defaults(vault_env) -> None:
    cfg = _load_with_autocommit(vault_env, {"enabled": True})
    a = cfg.auto_sync
    assert a.enabled is True
    assert a.interval_seconds == 30          # default preserved
    assert a.push is True
