"""L5 — orphan graph pages with zero inbound links. Graph-tree prefixes (live +
legacy) are single-sourced from the structure resolver (no path literals here)."""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from .. import structure as _structure
from .loader import Rule
from .runner import Finding, register_check


@register_check("check_orphan_pages")
def check_orphan_pages(
    conn: sqlite3.Connection, rule: Rule, space: Optional[str]
) -> List[Finding]:
    excluded = set(rule.extras.get("excluded_slugs") or [])
    prefixes = _structure.graph_prefixes()
    slug_clause = " OR ".join("p.slug LIKE ?" for _ in prefixes)
    slug_params = [f"{p}%" for p in prefixes]
    sql = f"""
        SELECT p.slug
        FROM   pages p
        LEFT   JOIN backlinks_count bc ON bc.page_id = p.id
        WHERE  ({slug_clause})
          AND  (bc.inbound_count IS NULL OR bc.inbound_count = 0)
    """
    findings: List[Finding] = []
    for r in conn.execute(sql, slug_params):
        if r["slug"] in excluded:
            continue
        findings.append(Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message="orphan: no inbound links from any other page",
            page_slug=r["slug"],
        ))
    return findings
