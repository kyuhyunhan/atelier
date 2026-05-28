"""PR-14: scaffold a new document under any subtree.

Templates:
- `product`  → workshop/products/<name>/README.md (extends the v0.1
  `new-product` command)
- `raw`      → raw/personal/inbox/<name>.md
- `note`     → workshop/notes/<name>.md
- `learning` → manual learnings/candidates/<date>/<name>.md (rare; the
  hook path is the preferred capture route)
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from ...util import config as _config


_TEMPLATES = ("product", "raw", "note", "learning")


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _builder_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local / "workshop"
    return cfg.space_by_role("builder-territory").local


def _now_iso_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _entry_id(rel: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"atelier:{rel}"))


def _write(path: Path, fm: Dict[str, Any], body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(str(path))
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")
    return path


def new_doc(*, template: str, name: str,
            role: str = "librarian-territory",
            fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if template not in _TEMPLATES:
        raise ValueError(f"unknown template {template!r}; "
                         f"choose one of {_TEMPLATES}")
    fields = dict(fields or {})

    if template == "product":
        builder_root = _builder_root()
        product_dir = builder_root / "products" / name
        if product_dir.exists():
            raise FileExistsError(str(product_dir))
        product_dir.mkdir(parents=True)
        rel = f"workshop/products/{name}/README.md"
        fm = {
            "schema_version": 4,
            "entry_id": _entry_id(rel),
            "title": fields.get("title", name),
            "type": "product",
            "status": fields.get("status", "active"),
            "sensitivity": "private",
            "created": _now_iso_day(),
            "updated": _now_iso_day(),
            "summary": fields.get("summary", ""),
        }
        target = product_dir / "README.md"
        _write(target, fm, f"# {name}\n\n(product description)\n")
        return {"path": str(target), "template": template}

    if template == "note":
        builder_root = _builder_root()
        target = builder_root / "notes" / f"{name}.md"
        rel = f"workshop/notes/{name}.md"
        fm = {
            "schema_version": 4,
            "entry_id": _entry_id(rel),
            "title": fields.get("title", name),
            "type": "note",
            "sensitivity": "private",
            "created": _now_iso_day(),
        }
        _write(target, fm, fields.get("body", f"# {name}\n\n"))
        return {"path": str(target), "template": template}

    if template == "raw":
        vault = _vault_root()
        target = vault / "raw" / "personal" / "inbox" / f"{name}.md"
        rel = f"raw/personal/inbox/{name}.md"
        fm = {
            "schema_version": 4,
            "entry_id": _entry_id(rel),
            "title": fields.get("title", name),
            "sensitivity": fields.get("sensitivity", "private"),
            "created_at": [{
                "value": _now_iso(),
                "precision": "second",
                "timezone": "UTC",
            }],
            "embedded_assets": [],
            "word_count": 0,
            "source": fields.get("source", "manual"),
        }
        _write(target, fm, fields.get("body", ""))
        return {"path": str(target), "template": template}

    if template == "learning":
        vault = _vault_root()
        day = _now_iso_day()
        target = (vault / "learnings" / "candidates" / day
                  / f"{name}.md")
        rel = f"learnings/candidates/{day}/{name}.md"
        fm = {
            "schema_version": 4,
            "entry_id": _entry_id(rel),
            "captured_at": _now_iso(),
            "agent_kind": "manual",
            "hook": "manual",
            "status": "candidate",
            "ac_status": "pending",
            "observation_kind": fields.get("observation_kind", "feedback"),
            "ac_results": {},
            "links": [],
        }
        if fields.get("project_hint"):
            fm["project_hint"] = fields["project_hint"]
        body = fields.get("body",
                          "## Observation\n\n## Why this matters\n\n")
        _write(target, fm, body)
        return {"path": str(target), "template": template}

    # unreachable due to early validation, but keep mypy happy.
    raise RuntimeError("unknown template")
