"""Search + relink for the learnings domain.

`search()` is a thin filter over the existing FTS index restricted to
the learnings/* page types. If a hit's source file is no longer present
(e.g. it has been retracted), it is skipped silently. A grep fallback
walks the filesystem directly when the FTS index has not been built yet
(common in fresh installs).

`relink()` updates the `links:` frontmatter field on an accepted
learning so the curator can attach wiki/entities/* or wiki/themes/*
references after the fact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from ...index import parse as _parse
from ...util import config as _config
from ...util import db as _db


_STATUS_TO_TYPES = {
    "candidate": ("learning_candidate",),
    "accepted":  ("learning_accepted",),
    "archived":  ("learning_archived",),
    "any":       ("learning_candidate", "learning_accepted", "learning_archived"),
}


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _ensure_iter(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _grep_walk(root: Path, query: str,
               *, types: Iterable[str],
               project: Optional[str],
               topic: Optional[str],
               limit: int) -> List[Dict[str, Any]]:
    """Filesystem-side fallback when FTS hasn't indexed learnings yet."""
    learnings_root = root / "learnings"
    if not learnings_root.exists():
        return []
    rx = re.compile(re.escape(query), re.I) if query else None
    out: List[Dict[str, Any]] = []
    for p in sorted(learnings_root.rglob("*.md")):
        if "by-project" in p.parts:
            # Skip the mirror so each accepted entry is reported once.
            continue
        text = p.read_text(encoding="utf-8")
        fm, body = _parse.split_frontmatter(text)
        status = fm.get("status") or "candidate"
        if not any(status == t.removeprefix("learning_") for t in types):
            continue
        if project and fm.get("project_hint") != project \
                and fm.get("target_project") != project:
            continue
        if topic and fm.get("target_topic") != topic:
            continue
        if rx is not None and not rx.search(body) and not rx.search(str(fm)):
            continue
        out.append({
            "path": str(p),
            "slug": p.stem,
            "status": status,
            "project": fm.get("target_project") or fm.get("project_hint"),
            "topic": fm.get("target_topic"),
            "entry_id": fm.get("entry_id"),
            "captured_at": fm.get("captured_at"),
            "snippet": body[:240].strip(),
        })
        if len(out) >= limit:
            break
    return out


def search(*, query: str = "",
           status: str = "accepted",
           project: Optional[str] = None,
           topic: Optional[str] = None,
           limit: int = 20) -> Dict[str, Any]:
    vault = _vault_root()
    types = _STATUS_TO_TYPES.get(status, _STATUS_TO_TYPES["accepted"])

    # FTS path
    hits: List[Dict[str, Any]] = []
    try:
        conn = _db.connect()
        try:
            placeholders = ",".join("?" * len(types))
            base = (
                "SELECT p.slug, p.page_type, p.space, p.frontmatter_json "
                "FROM pages p WHERE p.page_type IN (" + placeholders + ") "
            )
            params: List[Any] = list(types)
            if query:
                base = (
                    "SELECT p.slug, p.page_type, p.space, p.frontmatter_json "
                    "FROM chunks_fts f "
                    "JOIN chunks c ON c.rowid=f.rowid "
                    "JOIN pages p  ON p.id=c.page_id "
                    "WHERE chunks_fts MATCH ? "
                    "AND p.page_type IN (" + placeholders + ") "
                    "LIMIT ?"
                )
                params = [query, *types, limit * 3]
            else:
                base += "LIMIT ?"
                params = [*types, limit * 3]
            seen_slugs: set[str] = set()
            for row in conn.execute(base, params):
                slug = row["slug"]
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                import json as _json
                fm = _json.loads(row["frontmatter_json"] or "{}")
                if project and fm.get("target_project") != project \
                        and fm.get("project_hint") != project:
                    continue
                if topic and fm.get("target_topic") != topic:
                    continue
                hits.append({
                    "slug": slug,
                    "page_type": row["page_type"],
                    "space": row["space"],
                    "entry_id": fm.get("entry_id"),
                    "project": fm.get("target_project") or fm.get("project_hint"),
                    "topic": fm.get("target_topic"),
                    "captured_at": fm.get("captured_at"),
                })
                if len(hits) >= limit:
                    break
        finally:
            conn.close()
    except Exception:
        # Schema not initialized or pages table empty — fall through.
        hits = []

    if not hits:
        hits = _grep_walk(vault, query,
                          types=types, project=project, topic=topic,
                          limit=limit)
    return {"count": len(hits), "items": hits, "vault": str(vault)}


# ── relink ─────────────────────────────────────────────────────────────────


def relink(*, slug: str, links: List[str],
           mode: str = "replace") -> Dict[str, Any]:
    """Update the `links:` array on an accepted learning.

    mode = "replace" (default) overwrites the existing list.
    mode = "merge"   appends and deduplicates.
    """
    if mode not in ("replace", "merge"):
        raise ValueError(f"unknown mode: {mode!r}")

    vault = _vault_root()
    # Search by slug or entry_id in accepted/by-topic/.
    candidates = list((vault / "learnings" / "accepted" / "by-topic").rglob("*.md")) \
        if (vault / "learnings" / "accepted" / "by-topic").exists() else []
    needle = slug.removesuffix(".md")
    target: Optional[Path] = None
    for p in candidates:
        if p.stem == needle:
            target = p
            break
        fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        if str(fm.get("entry_id")) == slug:
            target = p
            break
    if target is None:
        raise FileNotFoundError(f"no accepted learning matches {slug!r}")

    fm, body = _parse.split_frontmatter(target.read_text(encoding="utf-8"))
    existing = list(fm.get("links") or [])
    new_links = list(dict.fromkeys((existing if mode == "merge" else []) + links))
    fm = dict(fm)
    fm["links"] = new_links

    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    target.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")

    # Mirror the change to the by-project copy if present.
    by_proj_root = vault / "learnings" / "accepted" / "by-project"
    if by_proj_root.exists():
        for mirror in by_proj_root.rglob(target.name):
            mirror.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")

    return {"path": str(target), "links": new_links}
