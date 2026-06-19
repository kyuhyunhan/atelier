"""PR-14: scaffold a new document under any subtree.

Templates:
- `product`  → workshop/products/<name>/README.md (extends the v0.1
  `new-product` command)
- `raw`      → raw/personal/inbox/<name>.md
- `note`     → workshop/notes/<name>.md
- `learning` → RETIRED (RFC 0005 §7.1): operational learnings are born as a
  Claim via atelier_learning_capture; this template now redirects there.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from ...structure import resolver as _structure
from ...util import config as _config
from ..learnings import store as _store


_TEMPLATES = ("product", "raw", "note", "learning")


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _builder_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local / _structure.intake_dir("workshop")
    return cfg.space_by_role("builder-territory").local


def _now_iso_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _entry_id(*, created_at: str, discriminator: str) -> str:
    """Content-based entry_id for a NEW manual doc (RFC 0005 P1.3).

    Replaces the dropped path-based `atelier:{rel}` form: a fresh doc's id is
    derived from its own creation timestamp plus a stable discriminator (the
    user-supplied `name`), never from where it happens to sit on disk. This only
    affects NEWLY created docs — already-stored ids are never recomputed.
    """
    return _structure.entry_id(
        "source", created_at=created_at, discriminator=discriminator
    )


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
        created = _now_iso_day()
        fm = {
            "schema_version": 4,
            "entry_id": _entry_id(created_at=created, discriminator=name),
            "title": fields.get("title", name),
            "type": "product",
            "status": fields.get("status", "active"),
            "sensitivity": "private",
            "created": created,
            "updated": created,
            "summary": fields.get("summary", ""),
        }
        target = product_dir / "README.md"
        _write(target, fm, f"# {name}\n\n(product description)\n")
        return {"path": str(target), "template": template}

    if template == "note":
        builder_root = _builder_root()
        target = builder_root / "notes" / f"{name}.md"
        created = _now_iso_day()
        fm = {
            "schema_version": 4,
            "entry_id": _entry_id(created_at=created, discriminator=name),
            "title": fields.get("title", name),
            "type": "note",
            "sensitivity": "private",
            "created": created,
        }
        _write(target, fm, fields.get("body", f"# {name}\n\n"))
        return {"path": str(target), "template": template}

    if template == "raw":
        vault = _vault_root()
        # canonical content root (provenance) from the resolver; legacy raw/ only
        # for an un-renamed vault.
        canonical = _structure.content_root()
        legacy = _structure.legacy_content_root()
        personal = _structure.intake_subpath("personal")
        inbox = _structure.inbox_subpath()
        prov = legacy if (not (vault / canonical / personal).exists()
                          and (vault / legacy / personal).exists()) else canonical
        target = vault / prov / personal / inbox / f"{name}.md"
        created = _now_iso()
        fm = {
            "schema_version": 4,
            "entry_id": _entry_id(created_at=created, discriminator=name),
            "title": fields.get("title", name),
            "sensitivity": fields.get("sensitivity", "private"),
            "created_at": [{
                "value": created,
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
        # RFC 0005 §7.1: operational learnings are BORN AS A CLAIM, not scaffolded
        # as empty candidate files. The legacy candidate-file lifecycle is retired;
        # there is no "empty learning to fill in later" in the claim model. Route to
        # the canonical born-as-claim path instead of writing raw/learning/candidates/.
        raise ValueError(
            "new_doc template 'learning' is retired (RFC 0005 §7.1): operational "
            "learnings are born as a Claim. Use atelier_learning_capture(observation=, "
            "why=) — it mints a v7 claim (domain:operational, surfacing:query, "
            "ac_status:pending) + a thin session source.")

    # unreachable due to early validation, but keep mypy happy.
    raise RuntimeError("unknown template")
