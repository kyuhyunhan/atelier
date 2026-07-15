"""L8 — a claim derived from a private-domain source must be sensitivity: private.

The personal invariant, Policy 1 (2026-07): personal MAY be atomized under the
human gate, but the derived claims must never LEAK — `sensitivity: private` is
what keeps them behind the recall sensitivity_gate and outside the dev lens
(RFC 0006 ③). This rule is the audit half; the dream-synthesis guard in
`claims_io.write_synthesized_claim` is the write-time half. Lint is the layer
that catches what no write-time check can: direct agent/human markdown writes
(the atomize skill writes files, not engine APIs) and sources re-domained to
personal after their claims were minted.

Private domains come from the structure resolver (structure.yaml `atomize:`),
never hardcoded (hard rule #3).
"""
from __future__ import annotations

import sqlite3
from typing import List

from ..structure import resolver as _structure
from .loader import Rule
from .runner import Finding, register_check


@register_check("check_private_domain_claims")
def check_private_domain_claims(
    conn: sqlite3.Connection, rule: Rule, space: str,
) -> List[Finding]:
    domains = _structure.atomize_private_source_domains()
    if not domains:
        return []
    dom_clause = ", ".join("?" for _ in domains)
    # Every (claim, derived_from source) pair where the source sits in a
    # private domain but the claim is NOT sensitivity: private.
    sql = f"""
        SELECT c.slug AS claim_slug, s.slug AS source_slug,
               json_extract(c.frontmatter, '$.sensitivity') AS sens
        FROM pages c, json_each(c.frontmatter, '$.derived_from') j
        JOIN pages s ON json_extract(s.frontmatter, '$.entry_id') = j.value
                    AND json_extract(s.frontmatter, '$.kind') = 'source'
        WHERE json_extract(c.frontmatter, '$.kind') = 'claim'
          AND json_extract(s.frontmatter, '$.domain') IN ({dom_clause})
          AND COALESCE(json_extract(c.frontmatter, '$.sensitivity'), '')
              <> 'private'
    """
    findings: List[Finding] = []
    for r in conn.execute(sql, tuple(domains)):
        findings.append(Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message=(f"claim derived from private-domain source "
                     f"{r['source_slug']} must be sensitivity: private "
                     f"(is: {r['sens'] or '(absent)'})"),
            page_slug=r["claim_slug"],
        ))
    return findings
