"""PR-10: regenerate wiki/index.md from wiki/* page frontmatter.

Distinct from the DB reindex pipeline — this produces a human-readable
catalog under wiki/index.md. The categories follow the librarian overlay
(digests, sources, entities, themes, synthesis).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...index import parse as _parse
from ...util import config as _config


_SECTIONS = ("digests", "sources", "entities", "themes", "synthesis")


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _scan(wiki: Path, subdir: str) -> List[Tuple[str, Dict[str, Any]]]:
    d = wiki / subdir
    if not d.exists():
        return []
    pages: List[Tuple[str, Dict[str, Any]]] = []
    for f in sorted(d.glob("*.md")):
        fm, _ = _parse.split_frontmatter(f.read_text(encoding="utf-8"))
        if fm:
            pages.append((f.stem, fm))
    return pages


def _render(sections: Dict[str, List[Tuple[str, Dict[str, Any]]]]) -> str:
    today = dt.date.today().isoformat()
    total = sum(len(v) for v in sections.values())
    out: List[str] = []
    out.append("---")
    out.append("type: wiki_index")
    out.append(f"updated: {today}")
    out.append(f"page_count: {total}")
    out.append("---")
    out.append("")
    out.append("# wiki — index")
    out.append("")
    out.append(f"_{total} pages, regenerated {today}_")
    out.append("")
    for name in _SECTIONS:
        pages = sections.get(name, [])
        if not pages:
            continue
        out.append(f"## {name} ({len(pages)})")
        out.append("")
        for slug, fm in pages:
            title = fm.get("title") or slug
            out.append(f"- [[{slug}]] — {title}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def regen(*, role: str = "librarian-territory",
          dry_run: bool = False) -> Dict[str, Any]:
    vault = _vault_root()
    wiki = vault / "wiki"
    sections: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {
        name: _scan(wiki, name) for name in _SECTIONS
    }
    rendered = _render(sections)

    target = wiki / "index.md"
    changed = (not target.exists()) or target.read_text(encoding="utf-8") != rendered
    if changed and not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")

    return {
        "path": str(target),
        "page_count": sum(len(v) for v in sections.values()),
        "changed": changed,
        "dry_run": dry_run,
    }
