"""RFC 0009 §5 — the goal-program metrics: quantities a *deliberate reduction*
can be gated on.

These are NOT part of the census. The census answers "how is the graph
composed?" and INV-1 (`verify._census_kind_totals`) reads it as a monotone
"no node kind shrank" gate. A counter that a goal must drive DOWN — promote
eligibility from 830 to ~23 — would, sitting inside `census`, silently become a
no-shrink gate on the exact quantity the goal exists to reduce (RFC 0009 §3.3).
So they live in a sibling `metrics` block, invisible to INV-1 by construction,
and are gated instead by the contract's INTENT/ENVELOPE clauses.

Two disciplines carry most of the weight here:

**Thin wrappers, never re-implementations** (§3.2 rule 1). The counters ship in
the same change they score, so a builder who cannot move a number could redefine
it — implementing `promote_eligible` as `ac_status == 'passed'` alone drops the
born-accepted branch, reports 23, and passes every clause on a vault that still
proposes 830 claims. Each counter therefore calls the production predicate, and
`tests/test_metrics.py` asserts equality with the path the real feature uses.

**Abstention is key-absence, never a zero** (§5.4). A metric that cannot be
measured omits its key, so the contract evaluator raises on a clause naming it.
Emitting `0.0` instead would silently *pass* a ceiling bound — reporting green
for a lens that returned nothing at all, which is the vacuous-PASS this whole
program exists to prevent.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_SURFACES_YAML = (Path(__file__).resolve().parents[3]
                  / "schema" / "data" / "lens_surfaces.yaml")

# Overridable for tests; production reads the operator's real guard file.
_PII_PATTERNS_PATH = Path.home() / ".atelier" / "pii_patterns.txt"


# ── 5.1 promote eligibility ─────────────────────────────────────────────────

def promote_eligible(*, vault: Optional[Path] = None) -> Dict[str, Any]:
    """`{total, by_domain}` over `claims_io.is_promote_eligible`.

    Routed through `projection_counts.promote_eligible()` — already the thin
    wrapper §3.2 rule 1 prescribes — with the SAME filesystem fallback the
    feature uses. That fallback is not optional: `projection_counts` answers
    `None` on a cold DB, and under the abstain rule a `None` would become
    key-absence and abort the run (§5.1).
    """
    from . import claims_io as _claims
    from . import projection_counts as _pc

    by_domain: Dict[str, int] = {}
    projected = _pc.promote_eligible()
    if projected is not None:
        nodes = _pc._load_nodes()
        for fm in (nodes or {}).get("claims", []):
            if _claims.is_promote_eligible(fm):
                d = str(fm.get("domain") or "(absent)")
                by_domain[d] = by_domain.get(d, 0) + 1
        return {"total": projected, "by_domain": by_domain}

    total = 0
    for p in _claims.iter_claim_files(vault):
        got = _claims.read_claim(p)
        if got is None:
            continue
        fm, _ = got
        if _claims.is_promote_eligible(fm):
            total += 1
            d = str(fm.get("domain") or "(absent)")
            by_domain[d] = by_domain.get(d, 0) + 1
    return {"total": total, "by_domain": by_domain}


# ── 5.2 pending age ─────────────────────────────────────────────────────────

def _as_date(raw: Any) -> Optional[date]:
    s = str(raw or "")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def pending_age(*, as_of: date, vault: Optional[Path] = None) -> Dict[str, Any]:
    """Age distribution of `ac_status: pending` claims, in days.

    The count is the wrong gate: draining the recent items while a 38-day tail
    rots would satisfy it (RFC 0009 §2, point 2). `as_of` is a REQUIRED
    parameter, not `today` — this is the one wall-clock-derived metric, and a
    verifier re-run a day later must reach the same verdict on the same commits
    (§4.2), which is impossible if the counter reads the clock itself.
    """
    from . import claims_io as _claims
    from . import projection_counts as _pc

    fms: List[Dict[str, Any]]
    nodes = _pc._load_nodes()
    if nodes is not None:
        fms = [fm for fm in nodes["claims"]
               if str(fm.get("ac_status") or "").lower() == "pending"]
    else:
        fms = []
        for p in _claims.iter_claim_files(vault):
            got = _claims.read_claim(p)
            if got is None:
                continue
            fm, _ = got
            if str(fm.get("ac_status") or "").lower() == "pending":
                fms.append(fm)

    ages: List[int] = []
    for fm in fms:
        d = _as_date(fm.get("created_at") or fm.get("created"))
        if d is not None:
            ages.append((as_of - d).days)
    ages.sort()
    if not ages:
        return {"count": len(fms), "p50": 0, "max": 0}
    return {"count": len(fms), "p50": ages[len(ages) // 2], "max": ages[-1]}


# ── 5.3 guard liveness ──────────────────────────────────────────────────────

def guard_liveness(*, pii_patterns_path: Optional[Path] = None) -> Dict[str, Any]:
    """How many guard patterns are ACTIVE — not whether a file exists.

    RFC 0008 §6 specified the absent-file case deliberately (a no-op pass). The
    case it left unspecified is a file that exists carrying only comments: both
    absorb enforcement points and `scripts/setup` key on existence, so all three
    report healthy while scanning nothing. That is the live state of this vault
    (9 lines, 0 active), and it is the shape of defect this metric exists to
    make visible.
    """
    p = pii_patterns_path if pii_patterns_path is not None else _PII_PATTERNS_PATH
    if not p.is_file():
        return {"pii_active_patterns": 0, "pii_file_present": False}
    active = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            active += 1
    return {"pii_active_patterns": active, "pii_file_present": True}


# ── 5.5 lens surface coverage ───────────────────────────────────────────────

def _declared_surfaces() -> List[Dict[str, Any]]:
    data = yaml.safe_load(_SURFACES_YAML.read_text(encoding="utf-8"))
    return list(data.get("surfaces") or [])


def lens_surface_coverage() -> Dict[str, Any]:
    """Content-returning MCP surfaces that actually accept a `lens` argument.

    The denominator is schema data (§3.2 rule 2) so it cannot be shrunk to meet
    a bound. The numerator is **introspected from the live handler signature**
    rather than declared, so this file cannot claim coverage the code does not
    have — the counter reads `tools._h_<name>` and asks whether `lens` is a real
    parameter.
    """
    from .. import tools as _tools
    covered: List[str] = []
    missing: List[str] = []
    for entry in _declared_surfaces():
        name = str(entry.get("name") or "")
        handler = getattr(_tools, f"_h_{name}", None)
        if handler is None:
            missing.append(name)
            continue
        params = inspect.signature(handler).parameters
        (covered if "lens" in params else missing).append(name)
    return {"covered": len(covered), "total": len(covered) + len(missing),
            "covered_names": sorted(covered), "missing_names": sorted(missing)}


# ── the block ───────────────────────────────────────────────────────────────

def metrics(*, as_of: Optional[date] = None, vault: Optional[Path] = None,
            pii_patterns_path: Optional[Path] = None) -> Dict[str, Any]:
    """The `metrics` block of a baseline (RFC 0009 §5).

    `cross_project_noise` (§5.4) is deliberately ABSENT until its out-of-tree
    probe fixture lands in G3 — under the abstain rule that absence is the
    honest signal, and a contract naming it raises rather than reading a
    fabricated zero.
    """
    stamp = as_of or datetime.now(timezone.utc).date()
    return {
        "as_of": stamp.isoformat(),
        "promote_eligible": promote_eligible(vault=vault),
        "pending_age": pending_age(as_of=stamp, vault=vault),
        "guard_liveness": guard_liveness(pii_patterns_path=pii_patterns_path),
        "lens_surface_coverage": lens_surface_coverage(),
    }
