"""Independent verifier for the RFC 0006 program (§6).

Given a FROZEN baseline (the committed `0006-baseline.json`) and a rubric id, this
recomputes the after-state and decides PASS/FAIL. It is the stage a *separate*
agent runs — the builder never grades its own work — so it makes no changes and
takes no arguments that let a caller soften the bar beyond selecting the rubric.

Two integrity guards:
- **Frozen baseline.** It refuses to run against a baseline with uncommitted
  changes (`require_committed`), so nobody can regenerate the "before" *after* a
  change and diff against themselves. (Skippable only in tests.)
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
    # Later pillars extend the invariants with their §4.2 gate as built,
    # e.g. "P3_scoped": {"gates": [("dev_lens_no_personal", ...)]}.
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
