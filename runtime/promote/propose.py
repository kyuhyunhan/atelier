"""Propose promotion of workshop content into the gorae wiki.

A *proposal* is a markdown document at
~/.atelier/cache/promotions/{ts}-{slug}.md describing what would move where.
The user reviews/edits it, then runs `atelier promote apply <path>`.

v0.1 strategy: surface workshop pages that link into gorae heavily (high
cross-citation), as candidates whose insights deserve a synthesis page.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..util import config, db

PROMOTIONS_DIR = config.CACHE_DIR / "promotions"


def _candidates(conn: sqlite3.Connection, limit: int = 10) -> List[Dict[str, Any]]:
    """Workshop pages with the most outbound links into gorae space."""
    sql = """
        SELECT  p.slug   AS workshop_slug,
                p.title  AS title,
                COUNT(l.id) AS gorae_links
        FROM    pages p
        JOIN    links l   ON l.from_page = p.id
        JOIN    pages tgt ON tgt.id = l.to_page_id
        WHERE   p.space = 'workshop'
          AND   tgt.space = 'gorae'
        GROUP   BY p.id
        ORDER   BY gorae_links DESC
        LIMIT   ?
    """
    return [dict(r) for r in conn.execute(sql, (limit,))]


def propose_all() -> Dict[str, Any]:
    PROMOTIONS_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    try:
        cands = _candidates(conn)
    finally:
        conn.close()

    if not cands:
        return {"path": None, "candidates": 0,
                "note": "no workshop→gorae citations found"}

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = PROMOTIONS_DIR / f"{ts}-proposal.md"

    lines: List[str] = []
    lines.append(f"# Promotion proposal — {ts}")
    lines.append("")
    lines.append("Workshop pages with the strongest cross-citation into gorae,")
    lines.append("which may warrant a `wiki/synthesis/*.md` page authored by")
    lines.append("the Librarian.")
    lines.append("")
    lines.append("Review each row. For each one to promote, leave the `promote:` line")
    lines.append("as `true` and optionally edit `target_slug`. Run:")
    lines.append("")
    lines.append("    atelier promote apply " + str(path))
    lines.append("")
    for c in cands:
        slug_safe = c["workshop_slug"].replace("/", "-").replace(".md", "")
        lines.append("---")
        lines.append(f"source: {c['workshop_slug']}")
        lines.append(f"title: {c['title'] or '(untitled)'}")
        lines.append(f"gorae_citations: {c['gorae_links']}")
        lines.append(f"target_slug: wiki/synthesis/{slug_safe}.md")
        lines.append(f"promote: false")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return {"path": str(path), "candidates": len(cands)}
