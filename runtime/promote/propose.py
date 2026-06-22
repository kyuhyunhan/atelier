"""Propose claim promotions — RFC 0005 §7.1 (a FIELD transition, not a dir move).

`promote` = elevate a Claim's `surfacing` from `query` → `proactive`, **behind the
acceptance gate**: only claims that have passed acceptance (`ac_status: passed`)
are eligible. This is the §7.1 transition

    learnings/notes/ (accepted)  ==>  surfacing: proactive, ac_status: passed

expressed as a frontmatter change on the same node — the claim never moves
between directories (the old candidates/→notes/ move is retired).

A *proposal* is a markdown document at
~/.atelier/cache/promotions/{ts}-proposal.md listing each eligible claim by its
stable `entry_id`. The user reviews it, flips `promote: true` on the ones to
elevate, then runs `atelier promote apply <path>`. The engine performs no LLM
judgement here — eligibility is the deterministic acceptance gate; the human
curates which gated claims actually earn the proactive tier.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..service.learnings import claims_io as _claims
from ..util import config


def _promotions_dir() -> Path:
    """Resolved lazily so a test's monkeypatched CACHE_DIR is honoured."""
    return config.CACHE_DIR / "promotions"


def _eligible(limit: int = 50) -> List[Dict[str, Any]]:
    """Claims eligible for query→proactive promotion: surfacing == query AND
    ac_status == passed (the acceptance gate). Returns compact rows keyed by the
    stable entry_id (the link/ledger target), newest acceptance first by file
    order (sorted path)."""
    out: List[Dict[str, Any]] = []
    for p in _claims.iter_claim_files():
        got = _claims.read_claim(p)
        if got is None:
            continue
        fm, _ = got
        if _claims.surfacing_of(fm) != _claims.TIER_QUERY:
            continue
        if str(fm.get("ac_status") or "").lower() != "passed":
            continue
        out.append({
            "entry_id": str(fm.get("entry_id")),
            "statement": str(fm.get("statement") or "").strip(),
            "domain": fm.get("domain") or "",
            "project": fm.get("project") or "",
            "path": str(p),
        })
        if len(out) >= limit:
            break
    return out


def eligible_count(limit: int = 50) -> int:
    """Number of promotion-eligible claims (surfacing:query AND ac_status:passed).

    Public, read-only wrapper over `_eligible()` so callers (e.g. the unified
    nudge surface in `runtime/service/nudges.py`) get the salient count without
    reaching into a private function. `limit` caps the scan, matching
    `_eligible`/`propose_all` (a nudge only needs "≥1", not the exact tail)."""
    return len(_eligible(limit=limit))


def propose_all() -> Dict[str, Any]:
    promotions_dir = _promotions_dir()
    promotions_dir.mkdir(parents=True, exist_ok=True)
    cands = _eligible()

    if not cands:
        return {"path": None, "candidates": 0,
                "note": "no query+ac_status:passed claims await promotion"}

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = promotions_dir / f"{ts}-proposal.md"

    lines: List[str] = []
    lines.append(f"# Promotion proposal — {ts}")
    lines.append("")
    lines.append("Claims that PASSED acceptance (`ac_status: passed`) and are")
    lines.append("still on-query-only (`surfacing: query`). Promoting elevates")
    lines.append("them to `surfacing: proactive` (a FIELD transition, RFC 0005 §7.1).")
    lines.append("")
    lines.append("For each claim to promote, set `promote: true`. Then run:")
    lines.append("")
    lines.append("    atelier promote apply " + str(path))
    lines.append("")
    for c in cands:
        lines.append("---")
        lines.append(f"entry_id: {c['entry_id']}")
        lines.append(f"statement: {c['statement']}")
        lines.append(f"domain: {c['domain']}")
        if c["project"]:
            lines.append(f"project: {c['project']}")
        lines.append("promote: false")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return {"path": str(path), "candidates": len(cands)}
