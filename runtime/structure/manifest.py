"""Vault self-description — `.atelier-vault.yaml` (RFC 0006 §7① / Pillar ①).

Today the engine infers a vault's structural era from *which directories exist*
(`resolver` carries both `raw/` and legacy `provenance/` prefixes and matches
whatever is on disk). That is archaeology: there is no explicit statement of what
the vault IS. This manifest ends it — a small file at the vault root that declares
the structure version and a stable vault id, so future migrations can key on a
fact instead of guessing from layout.

It lives in the VAULT (the content repo), not the engine — it is per-vault
identity, not methodology (the lens vocabulary, which IS methodology, lives in
`schema/data/lenses.yaml`). Writing it is the one write this module makes, and it
is idempotent: `ensure()` never clobbers an existing id.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

MANIFEST_FILENAME = ".atelier-vault.yaml"

# The structural era this engine expects. Aligns with the node schema version
# (v7 atomic graph, RFC 0005). Bumped when the vault LAYOUT changes, not when
# node frontmatter does.
CURRENT_STRUCTURE_VERSION = 7


def manifest_path(vault: Path) -> Path:
    return Path(vault) / MANIFEST_FILENAME


def read(vault: Path) -> Optional[Dict[str, Any]]:
    """The manifest dict, or None if the vault has none yet (pre-P1 vaults)."""
    p = manifest_path(vault)
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def ensure(vault: Path, *, vault_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the manifest, creating it if absent. Idempotent: an existing
    manifest is returned untouched (the vault id, once minted, is stable — it may
    key R2 prefixes or cross-references later, exactly like node entry_ids)."""
    existing = read(vault)
    if existing is not None:
        return existing
    data = {
        "structure_version": CURRENT_STRUCTURE_VERSION,
        "vault_id": vault_id or str(uuid.uuid4()),
        "created": datetime.now(timezone.utc).date().isoformat(),
    }
    # Atomic create ('x'): if a concurrent writer already minted the manifest
    # between our read and here, don't clobber its (possibly different) vault_id
    # — re-read and return theirs. vault_id, once minted, is stable.
    try:
        with open(manifest_path(vault), "x", encoding="utf-8") as fh:
            fh.write(yaml.safe_dump(data, sort_keys=True, default_flow_style=False))
    except FileExistsError:                      # pragma: no cover - race only
        return read(vault) or data
    return data


def validate(vault: Path) -> Dict[str, Any]:
    """Check the manifest is present and its structure_version matches this
    engine. Returns `{ok, present, version_ok, detail}` — the ① manifest gate."""
    data = read(vault)
    if data is None:
        return {"ok": False, "present": False, "version_ok": False,
                "detail": "no .atelier-vault.yaml (run `atelier setup` or ensure())"}
    version_ok = data.get("structure_version") == CURRENT_STRUCTURE_VERSION
    return {
        "ok": bool(data.get("vault_id")) and version_ok,
        "present": True,
        "version_ok": version_ok,
        "detail": f"structure_version={data.get('structure_version')} "
                  f"vault_id={'set' if data.get('vault_id') else 'MISSING'}",
    }
