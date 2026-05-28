"""L1 — broken raw-links from wiki/ pages."""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from .loader import Rule
from .runner import Finding, register_check


@register_check("check_raw_links_exist")
def check_raw_links_exist(
    conn: sqlite3.Connection, rule: Rule, space: Optional[str]
) -> List[Finding]:
    """Wiki pages whose [[raw/...]] link does not resolve."""
    sql = """
        SELECT p.slug AS from_slug, l.to_target
        FROM   links l
        JOIN   pages p ON p.id = l.from_page
        WHERE  l.to_page_id IS NULL
          AND  l.to_target LIKE 'raw/%'
          AND  p.space = 'gorae'
          AND  p.slug LIKE 'wiki/%'
    """
    findings: List[Finding] = []
    for r in conn.execute(sql):
        findings.append(Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message=f"broken raw-link: {r['to_target']}",
            page_slug=r["from_slug"],
            details={"to_target": r["to_target"]},
        ))
    return findings
