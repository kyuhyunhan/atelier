"""L1 — broken provenance-links from graph/ pages (raw/→provenance/, wiki/→graph/
post-GP1; both prefixes covered for legacy vaults)."""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from .loader import Rule
from .runner import Finding, register_check


@register_check("check_raw_links_exist")
def check_raw_links_exist(
    conn: sqlite3.Connection, rule: Rule, space: Optional[str]
) -> List[Finding]:
    """Graph pages whose [[provenance/...]] (or legacy [[raw/...]]) link does not resolve."""
    sql = """
        SELECT p.slug AS from_slug, l.to_target
        FROM   links l
        JOIN   pages p ON p.id = l.from_page
        WHERE  l.to_page_id IS NULL
          AND  (l.to_target LIKE 'provenance/%' OR l.to_target LIKE 'raw/%')
          AND  (p.slug LIKE 'graph/%' OR p.slug LIKE 'wiki/%')
    """
    findings: List[Finding] = []
    for r in conn.execute(sql):
        findings.append(Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message=f"broken provenance-link: {r['to_target']}",
            page_slug=r["from_slug"],
            details={"to_target": r["to_target"]},
        ))
    return findings
