"""L3 — entity.source_count drift from actual inbound link count."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from ..util import config
from ..index import writeback
from .loader import Rule
from .runner import Finding, register_check, register_fix


@register_check("check_source_count")
def check_source_count(
    conn: sqlite3.Connection, rule: Rule, space: Optional[str]
) -> List[Finding]:
    tolerance = rule.extras.get("tolerance", 1)
    sql = """
        SELECT e.canonical_slug,
               CAST(COALESCE(json_extract(p.frontmatter, '$.source_count'), 0)
                    AS INTEGER) AS declared,
               COUNT(l.id) AS actual
        FROM   entities e
        JOIN   pages p ON p.slug = e.canonical_slug
        LEFT   JOIN links l ON l.to_page_id = p.id
        WHERE  p.page_type = 'entity'
        GROUP  BY e.canonical_slug
    """
    findings: List[Finding] = []
    for r in conn.execute(sql):
        declared = r["declared"] or 0
        actual = r["actual"] or 0
        if abs(declared - actual) > tolerance:
            findings.append(Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=f"source_count drift: declared={declared} actual={actual}",
                page_slug=r["canonical_slug"],
                details={"declared": declared, "actual": actual},
            ))
    return findings


@register_fix("fix_source_count_recount")
def fix_source_count_recount(conn: sqlite3.Connection, f: Finding) -> bool:
    """Update entity page's source_count frontmatter to match `actual`."""
    if not f.page_slug or "actual" not in f.details:
        return False
    cfg = config.load()
    sp = cfg.space_by_role("librarian-territory")
    path = sp.local / f.page_slug
    if not path.exists():
        return False
    return writeback.patch_frontmatter(path, {"source_count": f.details["actual"]})
