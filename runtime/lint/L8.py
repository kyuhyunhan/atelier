"""L8 — a claim derived from a PRIVATE source must be sensitivity: private.

The personal invariant, Policy 1 (2026-07): personal MAY be atomized under the
human gate, but the derived claims must never LEAK — `sensitivity: private` is
what keeps them behind the recall sensitivity_gate and outside the dev lens
(RFC 0006 ③). This rule is the audit half; the dream-synthesis guard in
`claims_io.write_synthesized_claim` and the atomize guard in
`claims_io.atomize_write` are the write-time halves. Lint is the layer that
catches what no write-time check can: direct agent/human markdown writes (the
atomize skill can write files, not only engine APIs), a source that became
private AFTER its claims were minted, and every abstain-on-miss path where a
write-time guard could not resolve the source.

"Private source" is TWO conditions, not one:

- the source's **domain** is a private lane (`personal`) — Policy 1, and
- the source's own **`sensitivity: private`** — RFC 0008 M4, which demotes an
  absorbed `type: user` memory or a PII-pattern hit while leaving it in the
  `operational` domain. Checking the domain alone would make this rule blind
  to exactly the case M3's write-time guard abstains on.

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
    dom_clause = ", ".join("?" for _ in domains) if domains else "NULL"
    # Every (claim, derived_from source) pair where the SOURCE is private —
    # by its domain (Policy 1) or by its own sensitivity (RFC 0008 M4) — but
    # the claim is NOT sensitivity: private.
    sql = f"""
        SELECT c.slug AS claim_slug, s.slug AS source_slug,
               json_extract(c.frontmatter, '$.sensitivity') AS sens,
               json_extract(s.frontmatter, '$.domain') AS src_domain,
               json_extract(s.frontmatter, '$.sensitivity') AS src_sens
        FROM pages c, json_each(c.frontmatter, '$.derived_from') j
        JOIN pages s ON json_extract(s.frontmatter, '$.entry_id') = j.value
                    AND json_extract(s.frontmatter, '$.kind') = 'source'
        WHERE json_extract(c.frontmatter, '$.kind') = 'claim'
          AND (json_extract(s.frontmatter, '$.domain') IN ({dom_clause})
               OR json_extract(s.frontmatter, '$.sensitivity') = 'private')
          AND COALESCE(json_extract(c.frontmatter, '$.sensitivity'), '')
              <> 'private'
    """
    findings: List[Finding] = []
    for r in conn.execute(sql, tuple(domains)):
        why = ("private-domain" if r["src_domain"] in domains
               else "sensitivity:private")
        findings.append(Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message=(f"claim derived from {why} source "
                     f"{r['source_slug']} must be sensitivity: private "
                     f"(is: {r['sens'] or '(absent)'})"),
            page_slug=r["claim_slug"],
        ))
    return findings
