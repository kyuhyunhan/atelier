"""L1 — broken content-tree links from graph pages. Both the live and legacy
prefixes are matched; all prefixes are single-sourced from the structure
resolver (no path literals here)."""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from .. import structure as _structure
from .loader import Rule
from .runner import Finding, register_check


def _like_clause(column: str, prefixes: tuple) -> tuple:
    """Build an OR-ed `<column> LIKE ?` clause + params for a set of dir prefixes."""
    clause = " OR ".join(f"{column} LIKE ?" for _ in prefixes)
    params = [f"{p}%" for p in prefixes]
    return f"({clause})", params


@register_check("check_raw_links_exist")
def check_raw_links_exist(
    conn: sqlite3.Connection, rule: Rule, space: Optional[str]
) -> List[Finding]:
    """Graph pages whose content-tree link (live or legacy form) does not resolve."""
    target_clause, target_params = _like_clause("l.to_target", _structure.content_prefixes())
    slug_clause, slug_params = _like_clause("p.slug", _structure.graph_prefixes())
    sql = f"""
        SELECT p.slug AS from_slug, l.to_target
        FROM   links l
        JOIN   pages p ON p.id = l.from_page
        WHERE  l.to_page_id IS NULL
          AND  {target_clause}
          AND  {slug_clause}
    """
    findings: List[Finding] = []
    for r in conn.execute(sql, [*target_params, *slug_params]):
        findings.append(Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message=f"broken provenance-link: {r['to_target']}",
            page_slug=r["from_slug"],
            details={"to_target": r["to_target"]},
        ))
    return findings
