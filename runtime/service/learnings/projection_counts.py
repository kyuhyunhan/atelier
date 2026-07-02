"""Nudge counts sourced from the DB projection instead of scanning markdown.

Hard rule #4 — *markdown is truth; the DB is a projection.* `reindex` already
parses every node's frontmatter into `pages.frontmatter` (stored as JSON), and
`classify` maps a v7 node's `kind` straight to `page_type` ∈ {claim, source,
entity}. So the nudge counts — which are aggregate queries over that frontmatter
— belong on the projection, exactly like search / facets / think already are.

The expensive part of the old path was never the O(N) loop; it was
`rglob + read_text + YAML-parse` per file across thousands of files. Here we
issue ONE indexed query returning pre-parsed JSON, then feed the dicts to the
SAME predicate helpers the filesystem scans use (`store.is_accepted_operational_claim`,
`claims_io.surfacing_of`, `principles._is_principle`, `atomize.unatomized_from_nodes`).
The DATA SOURCE changes; the predicate logic stays in one place, so the fast path
and the filesystem fallback can never disagree.

Each function returns `None` when the projection cannot answer (DB missing/empty,
or query error) so the caller falls back to its filesystem scan. Counts therefore
reflect the last `reindex` — a bounded staleness that is harmless for a cadence
nudge and matches the projection trade search/facets already accept.
"""
from __future__ import annotations

import json
from typing import List, Optional, Tuple

from ...util import db as _db


def _load_nodes() -> Optional[Tuple[List[dict], List[dict]]]:
    """(claim_frontmatters, source_frontmatters) from the projection, or None
    when the projection can't answer (no rows / DB absent / query error)."""
    try:
        conn = _db.connect()
    except Exception:
        return None
    try:
        rows = _db.fetchall(
            conn,
            "SELECT page_type, frontmatter FROM pages "
            "WHERE page_type IN ('claim','source')",
        )
    except Exception:
        return None
    finally:
        conn.close()
    if not rows:
        return None                      # cold/un-reindexed DB → fall back
    claims: List[dict] = []
    sources: List[dict] = []
    for r in rows:
        try:
            fm = json.loads(r["frontmatter"])
        except Exception:                # pragma: no cover - tolerant
            continue
        if not isinstance(fm, dict):
            continue
        if r["page_type"] == "claim":
            claims.append(fm)
        else:
            sources.append(fm)
    return claims, sources


def accepted_operational() -> Optional[int]:
    """Count of accepted operational claims (the dream cadence total)."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    from . import store as _store
    claims, _ = nodes
    return sum(1 for fm in claims if _store.is_accepted_operational_claim(fm))


def promote_eligible(limit: Optional[int] = None) -> Optional[int]:
    """Count of promotion-eligible claims (surfacing:query AND ac_status:passed).
    Capped at `limit` to match `promote.propose.eligible_count`'s contract."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    from . import claims_io as _c
    claims, _ = nodes
    n = sum(1 for fm in claims
            if _c.surfacing_of(fm) == _c.TIER_QUERY
            and str(fm.get("ac_status") or "").lower() == "passed")
    return min(n, limit) if limit is not None else n


def proposed_principles() -> Optional[int]:
    """Count of principle claims awaiting review (ac_status:pending)."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    from . import principles as _p
    claims, _ = nodes
    return sum(1 for fm in claims
               if _p._is_principle(fm) and _p._status_of(fm) == "proposed")


def unatomized_sources() -> Optional[int]:
    """Count of Source nodes with no derived Claim (the atomize backlog)."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    from . import atomize as _a
    claims, sources = nodes
    return _a.unatomized_from_nodes(sources, claims)
