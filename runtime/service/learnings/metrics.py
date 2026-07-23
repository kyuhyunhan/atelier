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

def _tally_eligible(fms: Any) -> Dict[str, Any]:
    """The ONE place an eligible claim becomes counts, shared by both data
    sources — `census.py`'s discipline, for the same reason: two paths that
    tally separately can disagree, and `sum(by_domain) != total` must not be
    representable.

    An earlier revision took `total` from one projection query and `by_domain`
    from a second, so a DB hiccup between them yielded `total: N` with an empty
    split.
    """
    from . import claims_io as _claims
    by_domain: Dict[str, int] = {}
    for fm in fms:
        if _claims.is_promote_eligible(fm):
            d = str(fm.get("domain") or "(absent)")
            by_domain[d] = by_domain.get(d, 0) + 1
    return {"total": sum(by_domain.values()), "by_domain": by_domain}


def promote_eligible(*, vault: Optional[Path] = None) -> Dict[str, Any]:
    """`{total, by_domain}` over `claims_io.is_promote_eligible` — the same
    predicate `promote.propose._eligible` uses, never a re-implementation
    (RFC 0009 §3.2 rule 1).

    Projection-first with the SAME filesystem fallback the feature uses. That
    fallback is not optional: `projection_counts` answers `None` on a cold DB,
    and under the abstain rule a `None` would become key-absence and abort the
    run (§5.1).

    Note this counts the UNCAPPED pool, while `propose_all()` only ever proposes
    `_eligible()`'s first 50. That is deliberate — the G2 contract is about how
    much is eligible, not how much one proposal happens to list — but the two
    numbers are not meant to reconcile.
    """
    from . import claims_io as _claims
    from . import projection_counts as _pc

    nodes = _pc._load_nodes()
    if nodes is not None:
        return _tally_eligible(nodes["claims"])

    fms = []
    for p in _claims.iter_claim_files(vault):
        got = _claims.read_claim(p)
        if got is not None:
            fms.append(got[0])
    return _tally_eligible(fms)


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
            # A claim created after `as_of` is age 0, not negative. Verifying
            # against a stale program anchor otherwise takes a max over
            # mixed-sign values, which is not a tail measurement at all.
            ages.append(max(0, (as_of - d).days))
    ages.sort()

    out: Dict[str, Any] = {"count": len(fms), "dated": len(ages)}
    if len(ages) < len(fms):
        # ABSTAIN — §5.4: key-absence, never a zero. An unmeasurable tail
        # returning `max: 0` would PASS a `≤ 7` ceiling while 36 undated claims
        # rot, and would even let `count` rise. That is precisely the defect
        # `cross_project_noise` is withheld to avoid; the same rule applies here.
        return out
    out["p50"] = ages[len(ages) // 2] if ages else 0
    out["max"] = ages[-1] if ages else 0
    return out


# ── 5.3 guard liveness ──────────────────────────────────────────────────────

def guard_liveness(*, pii_patterns_path: Optional[Path] = None) -> Dict[str, Any]:
    """How many guard patterns are ACTIVE — not whether a file exists.

    RFC 0008 §6 specified the absent-file case deliberately (a no-op pass). The
    case it left unspecified is a file that exists carrying only comments: both
    absorb enforcement points and `scripts/setup` key on existence, so all three
    report healthy while scanning nothing. That is the live state of this vault
    (9 lines, 0 active), and it is the shape of defect this metric exists to
    make visible.

    Zero here is a real measurement, not an abstention, and that is safe because
    G1's bound is a FLOOR (`≥ 1`): an unloaded guard fails it rather than
    passing. Contrast `pending_age`, whose bound is a ceiling — there an
    unmeasurable zero would pass, so it abstains instead.
    """
    p = pii_patterns_path if pii_patterns_path is not None else _PII_PATTERNS_PATH
    if not p.is_file():
        return {"pii_active_patterns": 0, "_file_present": False}
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # ABSTAIN, don't raise. This file is per-machine and user-managed: a
        # latin-1 regex or a permission change would otherwise propagate out of
        # `metrics()` → `baseline.generate()` → `verify_against()` and abort
        # every OTHER metric and every global invariant along with it.
        return {"_file_present": True, "_unreadable": True}
    active = 0
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            active += 1
    return {"pii_active_patterns": active, "_file_present": True}


# ── 5.5 lens surface coverage ───────────────────────────────────────────────

def _declared_surfaces() -> Optional[List[Dict[str, Any]]]:
    """The declared surface list, or None when it cannot be read.

    None rather than an exception: this file is a new hard dependency of
    `baseline.generate()`, which `verify.verify_against()` calls. A raised
    `FileNotFoundError` here would abort the entire verification — the four
    unrelated metrics and every global invariant with it — where abstaining on
    one key is the specified behaviour (§5.4).
    """
    try:
        data = yaml.safe_load(_SURFACES_YAML.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    surfaces = data.get("surfaces")
    return list(surfaces) if isinstance(surfaces, list) else None


def lens_param_present() -> Optional[Dict[str, Any]]:
    """Content-returning MCP surfaces whose handler **accepts** a `lens`
    argument — a signature-level fact, and named for exactly that.

    An earlier revision called this `lens_surface_coverage` and RFC 0009 §5.5
    defined it as surfaces that "accept **and honour**" a lens. Only the first
    half is decidable from a signature, and the gap is not academic: with a
    contract bound of `covered = 6`, adding `lens: str = "dev"` to five handlers
    and discarding the value satisfies INTENT, ENVELOPE and INVARIANT while
    `session_bootstrap` still pushes personal claims into every dev session.
    That is the vacuous PASS this program exists to prevent, on the one counter
    §3.2 rule 2 singles out — so the metric is named for what it can prove, and
    **honouring is a behavioural gate G3 must add** (the shape already exists:
    `verify._check_dev_lens_no_personal` calls the retrieval path and inspects
    what comes back).

    The denominator stays schema data (§3.2 rule 2) so it cannot be shrunk to
    meet a bound; the numerator is introspected, so the yaml cannot claim a
    parameter the code does not have. Returns None (→ key omitted) when the
    declaration is unreadable.
    """
    from .. import tools as _tools
    declared = _declared_surfaces()
    if declared is None:
        return None
    present: List[str] = []
    absent: List[str] = []
    unimplemented: List[str] = []
    for entry in declared:
        name = str(entry.get("name") or "")
        handler = getattr(_tools, f"_h_{name}", None)
        if handler is None:
            # A DECLARED surface with no handler is a typo in the yaml, not a
            # missing lens. Folding the two together would cap `covered`
            # permanently with nothing in the output to say why.
            unimplemented.append(name)
            continue
        params = inspect.signature(handler).parameters
        (present if "lens" in params else absent).append(name)
    total = len(present) + len(absent) + len(unimplemented)
    return {"covered": len(present), "total": total,
            "unimplemented": len(unimplemented),
            "_present": sorted(present), "_absent": sorted(absent),
            "_unimplemented": sorted(unimplemented)}


# ── the block ───────────────────────────────────────────────────────────────

def metrics(*, as_of: Optional[date] = None, vault: Optional[Path] = None,
            pii_patterns_path: Optional[Path] = None) -> Dict[str, Any]:
    """The `metrics` block of a baseline (RFC 0009 §5).

    Two shape rules follow from §3.4, which makes ENVELOPE default-deny over
    "the leaf keys under `metrics`":

    - **Capture metadata does not live here.** `as_of` changes on every round
      baseline by construction, and §3.5 requires a waiver to carry a *numeric*
      bound — so an `as_of` leaf would trip default-deny on every run with no
      legal waiver shape. It belongs beside `captured_date` at the top level.
    - **Diagnostic leaves are `_`-prefixed** (`_present`, `_absent`,
      `_file_present`). They are lists and booleans, which cannot carry a
      numeric bound either. The prefix is a READABILITY convention, not the
      exclusion mechanism: §5.1.1 makes the rule "`_`-prefixed **or**
      non-numeric", because the frozen `0006-baseline.json` already carries
      unprefixed non-numeric leaves (`eval.engine`, `eval.paraphrase.stale`)
      that can never be renamed.

    `cross_project_noise` (§5.4) is deliberately ABSENT until its out-of-tree
    probe fixture lands in G3 — under the abstain rule that absence is the
    honest signal, and a contract naming it raises rather than reading a
    fabricated zero. Any counter that cannot measure omits its key the same way.
    """
    stamp = as_of or datetime.now(timezone.utc).date()
    out: Dict[str, Any] = {
        "promote_eligible": promote_eligible(vault=vault),
        "pending_age": pending_age(as_of=stamp, vault=vault),
        "guard_liveness": guard_liveness(pii_patterns_path=pii_patterns_path),
    }
    lens = lens_param_present()
    if lens is not None:
        out["lens_param_present"] = lens
    return out
