"""Apply a reviewed promotion proposal — RFC 0005 §7.1 field transition.

Parses the proposal markdown, finds rows with `promote: true`, locates each claim
by its stable `entry_id`, and transitions its `surfacing` from `query` →
`proactive` IN PLACE (`generated_by: promote`, `content_hash` re-derived, entry_id
preserved). No directory move — the old candidates/→notes/ + wiki/synthesis writer
is retired.

A PROMOTION_LOG.md is appended in ~/.atelier/cache/.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..service.learnings import claims_io as _claims
from ..util import config


def _promotion_log() -> Path:
    """Resolved lazily so a test's monkeypatched CACHE_DIR is honoured."""
    return config.CACHE_DIR / "PROMOTION_LOG.md"


def _parse_proposal(path: Path) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    cur: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "---":
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        m = re.match(r"^([a-z_]+):\s*(.*)$", line.strip())
        if m and cur is not None:
            cur[m.group(1)] = m.group(2)
    if cur:
        blocks.append(cur)
    return [b for b in blocks if b.get("entry_id")]


def apply_proposal(path: Path) -> Dict[str, Any]:
    blocks = _parse_proposal(path)
    selected = [b for b in blocks
                if b.get("promote", "false").lower() == "true"]
    promoted: List[str] = []
    skipped: List[Dict[str, str]] = []

    for b in selected:
        eid = b["entry_id"]
        found = _claims.find_claim_by_entry_id(eid)
        if found is None:
            skipped.append({"entry_id": eid, "reason": "not-found"})
            continue
        claim_path, fm, body = found
        if _claims.surfacing_of(fm) != _claims.TIER_QUERY:
            # Already promoted (or beyond) — idempotent skip.
            skipped.append({"entry_id": eid, "reason": "not-query-tier"})
            continue
        # Acceptance gate (defence in depth — propose already filtered, but the
        # proposal could be stale/hand-edited). SAME domain-aware predicate as
        # propose/projection: operational needs ac_status:passed, atomize-born
        # knowledge is born-accepted, private never promotes. (surfacing already
        # checked above; is_promote_eligible re-checks it harmlessly.)
        if not _claims.is_promote_eligible(fm):
            skipped.append({"entry_id": eid, "reason": "acceptance-gate"})
            continue
        _claims.set_surfacing(claim_path, fm, body,
                              new_tier=_claims.TIER_PROACTIVE,
                              generated_by="promote")
        promoted.append(eid)

    _append_log(path, promoted, skipped)
    return {
        "applied": bool(promoted),
        "promoted": promoted,
        "selected": len(selected),
        "skipped": len(skipped),
        "skipped_detail": skipped,
    }


def _append_log(proposal_path: Path, promoted: List[str],
                skipped: List[Dict[str, str]]) -> None:
    log = _promotion_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    lines = [f"\n## [{ts}] proposal={proposal_path.name}"]
    for eid in promoted:
        lines.append(f"- PROMOTE  {eid}  query→proactive")
    for s in skipped:
        lines.append(f"- SKIP     {s['entry_id']}  ({s['reason']})")
    with log.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
