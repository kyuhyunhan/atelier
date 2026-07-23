"""Independent verifier for the RFC 0006 program (§6).

Given a FROZEN baseline (the committed `0006-baseline.json`) and a rubric id, this
recomputes the after-state and decides PASS/FAIL. It is the stage a *separate*
agent runs — the builder never grades its own work — so it makes no changes and
takes no arguments that let a caller soften the bar beyond selecting the rubric.

Two integrity guards:
- **Frozen baseline.** It refuses to run against a baseline with uncommitted
  changes (`require_committed`), so nobody can regenerate the "before" *after* a
  change and diff against themselves. **Two known limits** (RFC 0009 §3.1, which
  closes both for contract-mode runs): `atelier verify --allow-uncommitted` is a
  public CLI flag, not a test-only affordance; and `git status` proves a file is
  *clean*, not *old*, so committing a regenerated baseline defeats the guard.
  RFC 0006 §6 specified "dirty **or** newer than the tag" — only the first
  half shipped here.
- **No self-grading.** The checks are fixed per rubric; a rubric can add checks
  but the global invariants (INV-1..4) always apply.

Scope note on INV-4: the committed baseline stores the surfacing *aggregate*
(total/visible/dark_count), so here INV-4 is the coarse "omission did not regress
vs baseline". The stronger per-entry `newly_dark` gate (`eval.gate`) runs inside
the workflow harness, which takes full snapshots around the change transiently.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import baseline as _baseline

# A tiny tolerance so floating-point equality on an unchanged vault is a PASS.
_EPS = 1e-9


# ── individual checks: (before, after) → (ok, detail) ───────────────────────

def _census_kind_totals(census: Dict[str, Any]) -> Dict[str, int]:
    """Total node count per kind, summed across one field (any field's counts
    sum to the node total for that kind)."""
    out: Dict[str, int] = {}
    for kind, fields in census.items():
        if not fields:
            out[kind] = 0
            continue
        any_field = next(iter(fields.values()))
        out[kind] = sum(any_field.values())
    return out


def _check_no_data_loss(before: Dict, after: Dict) -> Tuple[bool, str]:
    """INV-1 proxy: no node kind shrank (a drop implies vault content vanished)."""
    b = _census_kind_totals(before.get("census", {}))
    a = _census_kind_totals(after.get("census", {}))
    shrank = {k: (b[k], a.get(k, 0)) for k in b if a.get(k, 0) < b[k]}
    if shrank:
        return False, f"node kinds shrank vs baseline: {shrank}"
    return True, f"kinds ok (before={b}, after={a})"


def _check_no_omission_regression(before: Dict, after: Dict) -> Tuple[bool, str]:
    """INV-4 (aggregate): dark_count did not rise and visible did not fall."""
    b, a = before.get("surfacing", {}), after.get("surfacing", {})
    if a.get("dark_count", 0) > b.get("dark_count", 0):
        return False, f"dark_count rose {b.get('dark_count')}→{a.get('dark_count')}"
    if a.get("visible", 0) < b.get("visible", 0):
        return False, f"visible fell {b.get('visible')}→{a.get('visible')}"
    return True, f"surfacing ok (dark {b.get('dark_count')}→{a.get('dark_count')})"


def _metric_not_regressed(path: List[str], label: str) -> Callable[[Dict, Dict], Tuple[bool, str]]:
    """Factory: assert a nested numeric metric did not fall below baseline."""
    def _get(d: Dict) -> float:
        cur: Any = d
        for key in path:
            cur = (cur or {}).get(key) if isinstance(cur, dict) else None
        return float(cur) if isinstance(cur, (int, float)) else 0.0

    def _check(before: Dict, after: Dict) -> Tuple[bool, str]:
        bv, av = _get(before), _get(after)
        if av + _EPS < bv:
            return False, f"{label} regressed {bv:.4f}→{av:.4f}"
        return True, f"{label} ok ({bv:.4f}→{av:.4f})"
    return _check


def _check_engine_unchanged(before: Dict, after: Dict) -> Tuple[bool, str]:
    """Advisory (warn, not gate): a changed engine label means the embedding env
    differs, so metric comparisons are apples-to-oranges."""
    be, ae = before.get("engine"), after.get("engine")
    if be != ae:
        return False, f"engine changed {be!r}→{ae!r} (embedding env differs)"
    return True, f"engine unchanged ({be})"


# ── Pillar ① (Grounded) checks — vocabulary/manifest, not baseline-diff ──────

# Plausible (kind, domain) pairs the lens vocabulary must cover. Static (drawn
# from the schema enums + the claim-level `operational` convention) so the gate
# is a property of the vocabulary, not of the live census — it never entangles
# the frozen baseline. Impossible pairs (e.g. source/operational) are harmless:
# `full` covers them and the personal-leak check only inspects personal pairs.
_LENS_KINDS = ("claim", "source", "entity")
_LENS_DOMAINS = ("personal", "knowledge", "inbox", "workshop", "operational")


def _check_lens_coverage(before: Dict, after: Dict) -> Tuple[bool, str]:
    """Pillar ① gate: the lens vocabulary covers every plausible (kind, domain)
    and the dev lens excludes personal (the whole point of scoping)."""
    from ...structure import lenses as _lenses
    pairs = [(k, d) for k in _LENS_KINDS for d in _LENS_DOMAINS]
    v = _lenses.validate_coverage(pairs)
    if not v["ok"]:
        return False, (f"uncovered={v['uncovered']} "
                       f"dev_personal_leaks={v['dev_personal_leaks']}")
    return True, "lens vocabulary covers all pairs; dev excludes personal"


def _check_manifest(before: Dict, after: Dict) -> Tuple[bool, str]:
    """Pillar ① gate: the vault self-describes (a valid `.atelier-vault.yaml`)."""
    from ...structure import manifest as _manifest
    from . import cluster as _cl
    v = _manifest.validate(Path(_cl._vault_root()))
    return v["ok"], v["detail"]


def _check_forgets_flag_only(before: Dict, after: Dict) -> Tuple[bool, str]:
    """Pillar 4a gate: plan_forgets() is genuinely flag-only -- it must NEVER
    mutate ac_status itself (that stays a human decision via review.retract).
    Content-based, not count-based: a same-count swap (retract one, re-accept
    another) would slip past a bare count comparison, so this hashes each
    accepted file's (path, mtime, content_hash) and requires the exact SET to be
    unchanged -- not just its size."""
    from . import lateral as _lat
    from . import store as _store
    from . import cluster as _cl
    vault = Path(_cl._vault_root())

    def _fingerprint():
        return {str(p): (p.stat().st_mtime_ns, p.read_bytes())
                for p in _store.iter_accepted_files(vault)}

    before_fp = _fingerprint()
    plan = _lat.plan_forgets()
    after_fp = _fingerprint()
    if after_fp != before_fp:
        changed = sorted(set(before_fp) ^ set(after_fp)
                         | {k for k in before_fp if before_fp.get(k) != after_fp.get(k)})
        return False, f"plan_forgets mutated the accepted pool: {changed[:3]}"
    return True, (f"flag-only ok: {plan['candidate_count']}/{plan['total']} "
                  f"dark candidate(s) flagged, pool byte-identical")


def _check_dev_lens_no_personal(before: Dict, after: Dict) -> Tuple[bool, str]:
    """Pillar ③ gate: a dev-lens recall surfaces ZERO personal-domain claims.

    Fishes with a broad multi-term query so the corpus is actually exercised;
    the unit tests are the exhaustive guarantee. If the query returns nothing the
    check passes vacuously (nothing leaked) — this is a regression tripwire for
    the recall→lens wiring, not the primary proof."""
    from . import recall_v7 as _rv
    q = "session error data file project api rule change test note day"

    def _personal(hits):
        return [h for h in hits
                if str((h.get("fm") or {}).get("domain") or "") == "personal"]

    dev = _rv.rank_claims(q, None, tier="query", top_k=100, lens="dev")
    full = _rv.rank_claims(q, None, tier="query", top_k=100, lens="full")
    dev_personal = _personal(dev)
    full_personal = _personal(full)
    if dev_personal:
        return False, f"{len(dev_personal)} personal claim(s) leaked into the dev lens"
    # Non-vacuous when the corpus actually has personal claims for this query:
    # full must surface some that dev dropped. If none exist, pass but say so.
    if full_personal:
        return True, (f"dev excluded {len(full_personal)} personal claim(s) that "
                      f"full surfaced (dev {len(dev)} vs full {len(full)})")
    return True, (f"dev lens clean over {len(dev)} claim(s); no personal in the "
                  f"probe corpus (gate vacuous — unit tests are the guarantee)")


# ── rubric registry ─────────────────────────────────────────────────────────
# Each entry: gate checks (a fail → overall FAIL) + warn checks (advisory only).
# Global invariants apply to every rubric; a pillar rubric appends its own gate.

_INV_GATES: List[Tuple[str, Callable[[Dict, Dict], Tuple[bool, str]]]] = [
    ("no_data_loss", _check_no_data_loss),                       # INV-1
    ("no_omission_regression", _check_no_omission_regression),   # INV-4
    ("self_probe_recall", _metric_not_regressed(
        ["eval", "self_probe", "recall_at_k"], "self_probe R@k")),
    ("paraphrase_recall", _metric_not_regressed(
        ["eval", "paraphrase", "recall_at_k"], "paraphrase R@k")),
]
_WARNS: List[Tuple[str, Callable[[Dict, Dict], Tuple[bool, str]]]] = [
    ("engine_unchanged", _check_engine_unchanged),
]

_RUBRICS: Dict[str, Dict[str, Any]] = {
    "P0": {"description": "Foundation: no regression vs the frozen baseline.",
           "gates": [], "warns": []},
    "P1_grounded": {
        "description": "Pillar ①: lens vocabulary covers all pairs (dev excludes "
                       "personal) + vault self-describes, and no regression.",
        "gates": [("lens_coverage", _check_lens_coverage),
                  ("manifest", _check_manifest)],
        "warns": []},
    "P2_fresh": {
        "description": "Pillar ②: per-file change feed (reindex_path) + indexed "
                       "columns. Live gate is no-regression (the invariants); "
                       "change-feed parity, single-file freshness, and "
                       "routing-column presence are structural and locked by the "
                       "test suite (they need a fresh DB, so are not asserted "
                       "against a live un-rebuilt cache). Auto write-through on "
                       "write paths is a deliberate opt-in follow-up — eager "
                       "reindex shifts dream-cadence + cold-DB-fallback semantics.",
        "gates": [], "warns": []},
    "P3_scoped": {
        "description": "Pillar ③: a coding-session (dev-lens) recall excludes "
                       "personal, with no regression to operational recall.",
        "gates": [("dev_lens_no_personal", _check_dev_lens_no_personal)],
        "warns": []},
    "P4_curated": {
        "description": "Pillar ④: ④a forgetting is genuinely flag-only (no "
                       "auto-mutation); ④b hybrid retrieval (already live, RFC "
                       "0002) must not regress — covered by the paraphrase_recall "
                       "invariant already in every rubric.",
        "gates": [("forgets_flag_only", _check_forgets_flag_only)],
        "warns": []},
}


def _baseline_is_committed(path: Path) -> Tuple[bool, str]:
    """True when git reports `path` clean (tracked, no uncommitted change). If the
    file is not inside a git repo we cannot assert freshness — treat as NOT
    committed so the guard fails closed."""
    r = subprocess.run(["git", "status", "--porcelain", "--", str(path)],
                       cwd=str(path.parent), capture_output=True, text=True)
    if r.returncode != 0:
        return False, "not a git repo (cannot prove the baseline is frozen)"
    if r.stdout.strip():
        return False, f"baseline has uncommitted changes: {r.stdout.strip()[:80]}"
    return True, "baseline is committed"


def verify_against(baseline_path: Path, rubric_id: str = "P0", *,
                   vault: Optional[Path] = None,
                   require_committed: bool = True) -> Dict[str, Any]:
    """Recompute the after-state and score it against `baseline_path` under
    `rubric_id`. Returns a report dict with `passed` and per-check detail."""
    baseline_path = Path(baseline_path)
    if rubric_id not in _RUBRICS:
        raise KeyError(f"unknown rubric {rubric_id!r} (have {sorted(_RUBRICS)})")
    if not baseline_path.is_file():
        raise FileNotFoundError(f"no baseline at {baseline_path}")

    if require_committed:
        ok, detail = _baseline_is_committed(baseline_path)
        if not ok:
            raise RuntimeError(
                f"refusing to verify against a non-frozen baseline: {detail}. "
                "Commit the baseline first (or pass require_committed=False in tests).")

    before = json.loads(baseline_path.read_text(encoding="utf-8"))
    after = _baseline.generate(vault=vault, captured_date=before.get("captured_date"))

    rubric = _RUBRICS[rubric_id]
    gates = _INV_GATES + list(rubric.get("gates", []))
    warns = _WARNS + list(rubric.get("warns", []))

    checks: List[Dict[str, Any]] = []
    passed = True
    for name, fn in gates:
        ok, detail = fn(before, after)
        checks.append({"name": name, "severity": "gate", "ok": ok, "detail": detail})
        passed = passed and ok
    for name, fn in warns:
        ok, detail = fn(before, after)
        checks.append({"name": name, "severity": "warn", "ok": ok, "detail": detail})

    return {
        "passed": passed,
        "rubric": rubric_id,
        "baseline_captured": before.get("captured_date"),
        "engine_before": before.get("engine"),
        "engine_after": after.get("engine"),
        "checks": checks,
    }
