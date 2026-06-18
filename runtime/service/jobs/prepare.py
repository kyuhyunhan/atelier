"""PR-11: prepare_commit — pre-commit content-hygiene pipeline.

For each input markdown file:

1. resolve `entry_id: PENDING` (UUID5 from vault-relative path)
2. recalculate `word_count` from body
3. re-detect `embedded_assets` (image / attachment URLs in body)
4. append an `edited_at` entry when the body changed since the last
   `edited_at.value`

The proto-engine also drove LLM-based facets reclassification on every
body change. That path is **out of scope for v0.2** because it requires
runtime OpenAI credentials; it can be re-added in v0.3 once the LLM
gateway is shaped. The mechanical fields land in atelier today.
"""
from __future__ import annotations

import re
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from ...index import parse as _parse
from ...structure import resolver as _structure
from ...util import config as _config


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _new_uuid(rel: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"atelier:{rel}"))


_WORD_RX = re.compile(r"\w+", re.UNICODE)
_ASSET_RX = re.compile(
    r"!\[[^\]]*\]\(([^)\s]+)\)"             # markdown image
    r"|<img[^>]+src=\"([^\"]+)\""             # html img
)


def _word_count(body: str) -> int:
    # Strip code blocks before counting so we don't credit pasted source.
    stripped = re.sub(r"```.*?```", " ", body, flags=re.S)
    return len(_WORD_RX.findall(stripped))


def _detect_assets(body: str) -> List[str]:
    found: List[str] = []
    for m in _ASSET_RX.finditer(body):
        url = m.group(1) or m.group(2)
        if url and url not in found:
            found.append(url)
    return found


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _last_edited_value(fm: Dict[str, Any]) -> Optional[str]:
    arr = fm.get("edited_at") or []
    if isinstance(arr, list) and arr:
        last = arr[-1]
        if isinstance(last, dict):
            return last.get("value")
    return None


def _process_file(path: Path, vault: Path) -> Tuple[bool, Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    fm, body = _parse.split_frontmatter(text)
    original = dict(fm)
    rel = path.resolve().relative_to(vault.resolve()).as_posix()
    changed = False

    if str(fm.get("entry_id", "")).strip().upper() == "PENDING":
        fm["entry_id"] = _new_uuid(rel)
        changed = True

    new_wc = _word_count(body)
    if fm.get("word_count") != new_wc:
        fm["word_count"] = new_wc
        changed = True

    new_assets = _detect_assets(body)
    if fm.get("embedded_assets") != new_assets:
        fm["embedded_assets"] = new_assets
        changed = True

    last_edited = _last_edited_value(fm)
    body_signature = re.sub(r"\s+", "", body)
    edit_marker_signature = re.sub(r"\s+", "",
                                   original.get("_body_signature", ""))
    if (last_edited is None or edit_marker_signature != body_signature) and changed:
        fm.setdefault("edited_at", [])
        fm["edited_at"].append({
            "value": _now_iso(),
            "precision": "second",
            "timezone": "UTC",
        })

    return changed, fm


def _write(path: Path, fm: Dict[str, Any], body: str) -> None:
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")


def prepare_commit(*, paths: Optional[List[str]] = None,
                   dry_run: bool = False) -> Dict[str, Any]:
    vault = _vault_root()
    targets: Iterable[Path]
    if paths:
        targets = [Path(p) for p in paths]
    else:
        # canonical content root (provenance) from the resolver; legacy raw/
        # only for an un-renamed vault.
        canonical = vault / _structure.content_root()
        scan_root = canonical if canonical.exists() else (
            vault / _structure.legacy_content_root())
        targets = sorted(scan_root.rglob("*.md")) if scan_root.exists() else []

    modified: List[Dict[str, Any]] = []
    for p in targets:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        _, body = _parse.split_frontmatter(text)
        changed, new_fm = _process_file(p, vault)
        if changed:
            modified.append({"path": str(p), "fields": list(new_fm.keys())})
            if not dry_run:
                _write(p, new_fm, body)

    return {
        "vault": str(vault),
        "scanned": len(list(targets)) if not isinstance(targets, list) else len(targets),
        "modified": modified,
        "dry_run": dry_run,
    }
