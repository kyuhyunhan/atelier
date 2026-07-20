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

A guard keeps the accepted parity structural: if the vault still holds **legacy
`learning_accepted` notes** (the RFC 0001 flat store, which the accepted-pool
scan unions with graph/atomic claims), the accepted count **abstains** so the
filesystem fallback — which does that union — answers instead of silently
under-counting. (We deliberately do NOT add a `space` filter: the single-vault
model projects all of one physical vault's nodes into internal pseudo-spaces
`vault-librarian`/`vault-builder`, so there is no second vault to exclude, and
`page_type IN ('claim','source')` already scopes to the node kinds the
filesystem scans cover. A space filter would couple to reindex internals for no
gain today; revisit if a true multi-vault model lands.)

Each function returns `None` when the projection cannot answer (DB missing/empty,
query error, or an abstain guard) so the caller falls back to its filesystem
scan. Counts therefore reflect the last `reindex` — a bounded staleness that is
harmless for a cadence nudge and matches the projection trade search/facets
already accept.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ...util import db as _db


def _load_nodes() -> Optional[Dict[str, Any]]:
    """`{claims, sources, has_legacy_accepted}` from the projection, or None when
    it can't answer (no rows / DB absent / query error). `learning_accepted` rows
    are not parsed — their mere presence flips `has_legacy_accepted`, which makes
    the accepted count abstain (see module docstring)."""
    try:
        conn = _db.connect()
    except Exception:
        return None
    try:
        rows = _db.fetchall(
            conn,
            "SELECT page_type, frontmatter FROM pages "
            "WHERE page_type IN ('claim','source','learning_accepted')",
        )
    except Exception:
        return None
    finally:
        conn.close()
    if not rows:
        return None                          # cold/un-reindexed DB → fall back
    claims: List[dict] = []
    sources: List[dict] = []
    has_legacy_accepted = False
    for r in rows:
        pt = r["page_type"]
        if pt == "learning_accepted":
            has_legacy_accepted = True       # legacy RFC 0001 flat notes present
            continue
        try:
            fm = json.loads(r["frontmatter"])
        except Exception:                    # pragma: no cover - tolerant
            continue
        if not isinstance(fm, dict):
            continue
        if pt == "claim":
            claims.append(fm)
        elif pt == "source":
            sources.append(fm)
    return {"claims": claims, "sources": sources,
            "has_legacy_accepted": has_legacy_accepted}


def accepted_operational() -> Optional[int]:
    """Count of accepted operational claims (the dream cadence total).

    Abstains (→ filesystem fallback) when legacy `learning_accepted` notes exist,
    because the accepted pool unions those with graph/atomic claims and the
    projection query above only counts claims."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    if nodes["has_legacy_accepted"]:
        return None
    from . import store as _store
    return sum(1 for fm in nodes["claims"]
               if _store.is_accepted_operational_claim(fm))


def proactive_count() -> Optional[int]:
    """Count of proactive-tier claims (the dream cadence total — dream's actual
    input, ANY domain). Returns None on a cold DB → filesystem fallback."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    from . import claims_io as _c
    return sum(1 for fm in nodes["claims"]
               if _c.surfacing_of(fm) == _c.TIER_PROACTIVE)


def promote_eligible(limit: Optional[int] = None) -> Optional[int]:
    """Count of promotion-eligible claims — the SAME domain-aware gate as the
    filesystem scan (`claims_io.is_promote_eligible`), so projection and scan
    can't disagree. Capped at `limit` to match `eligible_count`'s contract."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    from . import claims_io as _c
    n = sum(1 for fm in nodes["claims"] if _c.is_promote_eligible(fm))
    return min(n, limit) if limit is not None else n


def proposed_principles() -> Optional[int]:
    """Count of principle claims awaiting review (ac_status:pending)."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    from . import principles as _p
    return sum(1 for fm in nodes["claims"]
               if _p._is_principle(fm) and _p._status_of(fm) == "proposed")


def unatomized_sources() -> Optional[int]:
    """Count of Source nodes with no derived Claim (the atomize backlog)."""
    nodes = _load_nodes()
    if nodes is None:
        return None
    from . import atomize as _a
    return _a.unatomized_from_nodes(nodes["sources"], nodes["claims"])
