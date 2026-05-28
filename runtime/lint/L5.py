"""L5 — orphan wiki pages with zero inbound links."""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from .loader import Rule
from .runner import Finding, register_check


@register_check("check_orphan_pages")
def check_orphan_pages(
    conn: sqlite3.Connection, rule: Rule, space: Optional[str]
) -> List[Finding]:
    excluded = set(rule.extras.get("excluded_slugs") or [])
    sql = """
        SELECT p.slug
        FROM   pages p
        LEFT   JOIN backlinks_count bc ON bc.page_id = p.id
        WHERE  p.space = 'gorae'
          AND  p.slug LIKE 'wiki/%'
          AND  (bc.inbound_count IS NULL OR bc.inbound_count = 0)
    """
    findings: List[Finding] = []
    for r in conn.execute(sql):
        if r["slug"] in excluded:
            continue
        findings.append(Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message="orphan: no inbound links from any other page",
            page_slug=r["slug"],
        ))
    return findings
