"""L6 — stale raw sources without a corresponding wiki/sources page."""
from __future__ import annotations

import fnmatch
import sqlite3
from typing import List, Optional

from .loader import Rule
from .runner import Finding, register_check


def _matches_any(slug: str, patterns: List[str]) -> bool:
    return any(_glob(p, slug) for p in patterns)


def _glob(pattern: str, slug: str) -> bool:
    if "**" not in pattern:
        return fnmatch.fnmatchcase(slug, pattern)
    import re
    rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return bool(re.fullmatch(rx, slug))


@register_check("check_stale_sources")
def check_stale_sources(
    conn: sqlite3.Connection, rule: Rule, space: Optional[str]
) -> List[Finding]:
    exempt_patterns = [
        e.get("path_pattern") for e in (rule.extras.get("exemptions") or [])
    ]
    exempt_patterns = [p for p in exempt_patterns if p]

    # Collect existing wiki/source slugs by basename (sans extension)
    source_basenames = {
        r["slug"].split("/")[-1].rsplit(".", 1)[0]
        for r in conn.execute(
            "SELECT slug FROM pages WHERE space='gorae' AND page_type='source'"
        )
    }

    findings: List[Finding] = []
    for r in conn.execute(
        "SELECT slug FROM pages WHERE space='gorae' AND page_type='raw_source' "
        "ORDER BY slug"
    ):
        slug = r["slug"]
        if _matches_any(slug, exempt_patterns):
            continue
        basename = slug.split("/")[-1].rsplit(".", 1)[0]
        if basename in source_basenames:
            continue
        findings.append(Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message="raw source has no wiki/sources/ summary",
            page_slug=slug,
        ))
    return findings
