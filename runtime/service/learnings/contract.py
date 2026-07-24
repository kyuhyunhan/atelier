"""RFC 0009 §3 — the delta contract evaluator.

A goal declares, before any code is written, exactly what it intends to change.
This module scores an (before, after) pair of baselines against that declaration
in three layers:

- **INTENT** — did the declared change happen? Each clause names a metric and a
  bound (`eq`/`max`/`min`/`delta`).
- **ENVELOPE** — did anything *else* move? Default-deny over a defined namespace
  (§3.4): every measurable leaf INTENT does not name must be unchanged, unless a
  waiver releases it — and a waiver carries a bound, never a bare exemption
  (§3.5).
- **INVARIANT** — the global gates still run (that is `verify.py`'s job). A
  contract may only *supersede* a named clause under §3.3, and only with a
  matching INTENT bound.

Two failure modes are kept strictly apart, because they route differently in the
loop (§6):

- a **FAIL** is a returned result — the change missed its target, and a fixer may
  try again.
- a **raise** (`ContractError`) is a hard abort — the harness cannot be trusted
  for this run: a metric key that no counter emits, a malformed clause, a
  non-numeric bound. Retrying would let a builder convert a broken integrity
  check into three chances at a green one, so it never reaches the fixer.

Everything here is a **pure function of (contract, before, after)** — no I/O, no
clock, no git. The freeze guards that decide *which* contract and *which* before
are trustworthy live in `freeze.py`; keeping them out of the evaluator is what
lets the evaluator be exhaustively property-tested.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# The baseline blocks whose leaves form the ENVELOPE namespace (§3.4). Ordered,
# but membership is what matters. `vault.content_fingerprint` is synthetic (it
# is not a numeric leaf) and is handled as a named special member below.
_NAMESPACE_BLOCKS = ("metrics", "census", "surfacing", "eval")

# The one non-numeric member the envelope still guards: a content hash cannot be
# an INTENT bound, so a vault-mutating goal reaches it only through a bounded
# waiver (§3.5). Absent from a pure-metrics baseline; present once G0c captures
# the fingerprint.
_FINGERPRINT_KEY = "vault.content_fingerprint"


class ContractError(Exception):
    """A raise, not a FAIL (§6). The harness is untrustworthy for this run —
    a clause names a key no counter emits, a bound is malformed, or a metric is
    present on one side and absent on the other. Never retried in-run."""


# ── leaf access ─────────────────────────────────────────────────────────────

_MISSING = object()


def _leaf(d: Dict[str, Any], dotted: str) -> Any:
    """Resolve `a.b.c` to a leaf value, or `_MISSING` if any segment is absent.

    Deliberately does NOT default to 0 — that is the `_metric_not_regressed._get`
    behaviour RFC 0009 §8.1.3 calls out: an absent key resolving to 0 lets a
    typo'd `{"eq": 0}` clause pass while proving nothing. Absence is a signal the
    caller must handle, never a silent zero.
    """
    cur: Any = d
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return _MISSING
        cur = cur[seg]
    return cur


def _is_number(v: Any) -> bool:
    # bool is an int subclass; a boolean leaf is diagnostic, not a metric.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ── the namespace (§3.4) ────────────────────────────────────────────────────

def _numeric_leaves(block: Any, prefix: str) -> Dict[str, Any]:
    """Every numeric leaf under `block`, keyed by dotted path.

    Excludes `_`-prefixed keys (a readability convention) AND non-numeric leaves
    (the actual rule, §5.1.1): the frozen 0006 anchor carries unprefixed strings
    and lists (`eval.engine`, `eval.paraphrase.stale`) that can never be renamed,
    so membership is "numeric and not underscore-prefixed", not the prefix alone.
    """
    out: Dict[str, Any] = {}
    if isinstance(block, dict):
        for k, v in block.items():
            if k.startswith("_"):
                continue
            path = f"{prefix}.{k}"
            if isinstance(v, dict):
                out.update(_numeric_leaves(v, path))
            elif _is_number(v):
                out[path] = v
    return out


def namespace(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    """The ENVELOPE namespace: the UNION of numeric leaf paths under the tracked
    blocks across both snapshots, plus the fingerprint if either side carries it.

    Union, never intersection (§3.4): under intersection semantics, *removing* a
    counter from the after-snapshot would drop it out of the envelope — the exact
    dodge default-deny closes, one level down. A leaf present on one side and
    absent from the other is not silently unchanged; the envelope check below
    turns that into a raise.
    """
    keys: set[str] = set()
    for block in _NAMESPACE_BLOCKS:
        keys.update(_numeric_leaves(before.get(block), block))
        keys.update(_numeric_leaves(after.get(block), block))
    if _leaf(before, _FINGERPRINT_KEY) is not _MISSING \
            or _leaf(after, _FINGERPRINT_KEY) is not _MISSING:
        keys.add(_FINGERPRINT_KEY)
    return sorted(keys)


# ── bound evaluation ────────────────────────────────────────────────────────

_BOUND_KINDS = ("eq", "max", "min", "delta")


def _check_bound(bound: Dict[str, Any], value: Any, *,
                 before_value: Any = _MISSING) -> Tuple[bool, str]:
    """Evaluate one numeric bound against a measured value.

    `delta` is relative to the before-value and therefore needs one; the others
    are absolute. A malformed bound (unknown kind, non-numeric target, or a
    non-numeric measured value) is a RAISE, not a FAIL — a contract that cannot
    be evaluated is a broken harness, not a missed target.
    """
    kinds = [k for k in _BOUND_KINDS if k in bound]
    if len(kinds) != 1:
        raise ContractError(
            f"a bound names exactly one of {_BOUND_KINDS}, got {sorted(bound)}")
    kind = kinds[0]
    target = bound[kind]
    if not _is_number(target):
        raise ContractError(f"bound {kind!r} target must be numeric, got {target!r}")
    if not _is_number(value):
        raise ContractError(f"measured value is not numeric: {value!r}")

    if kind == "eq":
        return value == target, f"{value} == {target}"
    if kind == "max":
        return value <= target, f"{value} <= {target}"
    if kind == "min":
        return value >= target, f"{value} >= {target}"
    # delta: measured change from the before-value equals target
    if not _is_number(before_value):
        raise ContractError("a `delta` bound needs a numeric before-value")
    return (value - before_value) == target, \
        f"Δ {value - before_value} == {target}"


# ── the three layers ────────────────────────────────────────────────────────

def _eval_intent(clauses: List[Dict[str, Any]], before: Dict, after: Dict,
                 ) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for clause in clauses:
        metric = clause.get("metric")
        bound = clause.get("to")
        if not isinstance(metric, str) or not isinstance(bound, dict):
            raise ContractError(f"malformed INTENT clause: {clause!r}")
        value = _leaf(after, metric)
        if value is _MISSING:
            # §8.1.3: a clause naming a key no counter emits is a broken
            # contract, not a satisfied one. RAISE.
            raise ContractError(
                f"INTENT names {metric!r}, absent from the after-snapshot")
        before_value = _leaf(before, metric)
        # `from`, when present, is an integrity check, not decoration: it asserts
        # the before-picture the contract was written against. A contract that
        # says `from: 830` verified against a before that reads 500 was authored
        # against a different baseline — RAISE rather than grade it.
        declared_from = clause.get("from")
        if declared_from is not None and before_value is not _MISSING \
                and before_value != declared_from:
            raise ContractError(
                f"INTENT {metric!r} declares from={declared_from} but the "
                f"before-snapshot reads {before_value} — wrong baseline")
        ok, detail = _check_bound(bound, value,
                                  before_value=before_value)
        results.append({"layer": "intent", "metric": metric,
                        "ok": ok, "detail": detail})
    return results


def _eval_envelope(envelope: Dict[str, Any], intent_metrics: set,
                   before: Dict, after: Dict) -> List[Dict[str, Any]]:
    mode = envelope.get("mode", "default-deny")
    if mode != "default-deny":
        raise ContractError(f"unknown envelope mode {mode!r} (only default-deny)")

    ns = set(namespace(before, after))

    # A waiver RELEASES one namespace metric from strict equality and instead
    # BOUNDS a metric — the same one, or a sibling (§3.5). The release/bound split
    # is load-bearing: `vault.content_fingerprint` is a hash string that cannot
    # carry a numeric bound, so a vault-mutating goal releases it and bounds
    # `vault.changed_paths.count` instead — "repaired 12 links" stays
    # distinguishable from "rewrote 400 files". A same-metric waiver omits
    # `bound.metric` and bounds the released metric itself.
    waivers: Dict[str, Dict[str, Any]] = {}
    for w in envelope.get("waivers", []):
        release = w.get("release")
        bound = w.get("bound")
        if not isinstance(release, str) or not isinstance(bound, dict):
            raise ContractError(
                f"a waiver needs `release` (a metric) and `bound` (a clause): {w!r}")
        bound_metric = bound.get("metric", release)
        bound_to = bound.get("to")
        if not isinstance(bound_metric, str) or not isinstance(bound_to, dict):
            raise ContractError(f"waiver `bound` needs a metric and a `to`: {w!r}")
        if not w.get("reason"):
            raise ContractError(f"waiver releasing {release!r} carries no reason (§3.5)")
        if release not in ns:
            # A waiver on a metric outside the namespace is inert — almost
            # certainly an author typo. Catch it at the Contract stage rather
            # than letting the underlying metric stay silently default-denied.
            raise ContractError(
                f"waiver releases {release!r}, which is not in the envelope "
                f"namespace — nothing to release (typo?)")
        waivers[release] = {"bound_metric": bound_metric, "to": bound_to}

    results: List[Dict[str, Any]] = []
    for metric in sorted(ns):
        if metric in intent_metrics:
            continue                                # owned by INTENT, not envelope
        bv, av = _leaf(before, metric), _leaf(after, metric)
        if bv is _MISSING or av is _MISSING:
            # union membership with one side absent → raise (§3.4). Dropping a
            # counter must not be a way out of the envelope.
            raise ContractError(
                f"{metric!r} is in the namespace but absent from "
                f"{'before' if bv is _MISSING else 'after'}")
        if metric in waivers:
            wv = waivers[metric]
            target = _leaf(after, wv["bound_metric"])
            if target is _MISSING:
                raise ContractError(
                    f"waiver bounds {wv['bound_metric']!r}, absent from the "
                    "after-snapshot")
            before_target = _leaf(before, wv["bound_metric"])
            ok, detail = _check_bound(wv["to"], target, before_value=before_target)
            results.append({"layer": "envelope", "metric": metric, "waived": True,
                            "ok": ok,
                            "detail": f"released; {wv['bound_metric']} {detail}"})
            continue
        ok = (av == bv)
        results.append({"layer": "envelope", "metric": metric, "waived": False,
                        "ok": ok, "detail": f"{av} == {bv}" if ok
                        else f"moved {bv} → {av}"})
    return results


def validate_supersedes(contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Shape-check the `supersedes` block and return its entries.

    §3.3: supersession is per-clause (invariant + metric + direction), and each
    entry requires a **matching INTENT bound** on the same metric — a contract
    that releases an invariant it did not also bound is disabling a gate it never
    earned. This validates the shape and the INTENT pairing; *applying* the
    release (skipping that specific invariant clause) happens where the
    invariants run, which is why this is separate from `evaluate`.
    """
    entries = contract.get("supersedes", [])
    if not isinstance(entries, list):
        raise ContractError("`supersedes` must be a list")
    intent_metrics = {c.get("metric") for c in contract.get("intent", [])
                      if isinstance(c, dict)}
    for e in entries:
        if not isinstance(e, dict):
            raise ContractError(f"malformed supersedes entry: {e!r}")
        inv, metric, direction = e.get("invariant"), e.get("metric"), e.get("direction")
        if not (inv and metric and direction):
            raise ContractError(
                f"a supersedes entry needs invariant/metric/direction: {e!r}")
        if direction not in ("may-fall", "may-rise"):
            raise ContractError(
                f"supersedes direction must be may-fall|may-rise, got {direction!r}")
        if not e.get("reason"):
            raise ContractError(f"supersedes {inv}/{metric} carries no reason")
        if metric not in intent_metrics:
            raise ContractError(
                f"supersedes {inv}/{metric} has no matching INTENT bound (§3.3) — "
                "it releases a gate the contract never earned")
    return entries


def evaluate(contract: Dict[str, Any], before: Dict[str, Any],
             after: Dict[str, Any]) -> Dict[str, Any]:
    """Score a contract against a (before, after) pair. Pure.

    Returns `{passed, intent, envelope}` where each layer is a list of per-clause
    results. Raises `ContractError` for any condition that means the harness
    cannot be trusted (§6): an unknown metric key, a malformed clause or bound, a
    non-default envelope mode, or a namespace leaf present on only one side.

    INVARIANT is not scored here — the global gates run in `verify.verify_against`
    (this evaluator adds the delta axis beside them). `supersedes` is
    shape-validated (a malformed or unearned release raises); *applying* a release
    happens where the invariants run.
    """
    intent_clauses = contract.get("intent", [])
    if not isinstance(intent_clauses, list):
        raise ContractError("`intent` must be a list")
    intent = _eval_intent(intent_clauses, before, after)
    intent_metrics = {c["metric"] for c in intent}

    envelope_spec = contract.get("envelope", {"mode": "default-deny"})
    if not isinstance(envelope_spec, dict):
        raise ContractError("`envelope` must be an object")
    envelope = _eval_envelope(envelope_spec, intent_metrics, before, after)

    validate_supersedes(contract)                    # raise on a malformed release

    passed = all(r["ok"] for r in intent) and all(r["ok"] for r in envelope)
    return {"passed": passed, "intent": intent, "envelope": envelope}
