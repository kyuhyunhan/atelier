"""RFC 0009 §3 — the delta-contract evaluator.

The evaluator is pure, so it can be pinned exhaustively with synthetic
before/after dicts — no vault, no git. The tests fall into three groups:

- INTENT bounds evaluate correctly, and an unknown/typo'd metric key RAISES
  rather than resolving to 0 (the `_metric_not_regressed._get` defect, §8.1.3).
- ENVELOPE is default-deny over a UNION namespace: an unintended move fails, a
  dropped counter raises, a bounded waiver releases exactly one metric.
- The layer separation holds: a FAIL is returned, a broken harness raises.
"""
from __future__ import annotations

import pytest

from runtime.service.learnings import contract as _c
from runtime.service.learnings.contract import ContractError, evaluate, namespace


def _b(**metrics):
    """A minimal baseline carrying just a `metrics` block."""
    return {"metrics": metrics}


# ── INTENT bounds ────────────────────────────────────────────────────────────

def test_intent_max_passes_and_fails():
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}]}
    assert evaluate(ct, _b(x=830), _b(x=23))["passed"] is True
    assert evaluate(ct, _b(x=830), _b(x=40))["passed"] is False


def test_intent_eq_and_min():
    eq = {"intent": [{"metric": "metrics.x", "to": {"eq": 0}}]}
    assert evaluate(eq, _b(x=807), _b(x=0))["passed"] is True
    assert evaluate(eq, _b(x=807), _b(x=1))["passed"] is False
    mn = {"intent": [{"metric": "metrics.x", "to": {"min": 1}}]}
    assert evaluate(mn, _b(x=0), _b(x=1))["passed"] is True
    assert evaluate(mn, _b(x=0), _b(x=0))["passed"] is False


def test_intent_delta_uses_the_before_value():
    ct = {"intent": [{"metric": "metrics.x", "to": {"delta": -10}}]}
    assert evaluate(ct, _b(x=100), _b(x=90))["passed"] is True
    assert evaluate(ct, _b(x=100), _b(x=95))["passed"] is False


def test_unknown_metric_key_raises_not_passes():
    """§8.1.3: the live `_metric_not_regressed._get` returns 0.0 for an
    unresolved path, so `metrics.knowledge` under `{"eq": 0}` would PASS while
    proving nothing. The evaluator must RAISE instead."""
    ct = {"intent": [{"metric": "metrics.by_domain.knowledge", "to": {"eq": 0}}]}
    # after emits the RIGHT shape but the clause names a typo'd path
    after = {"metrics": {"by_domain": {"knowledge_TYPO": 0}}}
    with pytest.raises(ContractError, match="absent from the after-snapshot"):
        evaluate(ct, _b(x=1), after)


def test_a_malformed_bound_raises():
    for bad in ({"max": 1, "min": 2}, {}, {"max": "lots"}, {"nope": 1}):
        with pytest.raises(ContractError):
            evaluate({"intent": [{"metric": "metrics.x", "to": bad}]},
                     _b(x=5), _b(x=5))


def test_delta_without_a_numeric_before_raises():
    ct = {"intent": [{"metric": "metrics.x", "to": {"delta": -1}}]}
    with pytest.raises(ContractError, match="delta"):
        evaluate(ct, {"metrics": {}}, _b(x=4))       # x absent from before


# ── the namespace (§3.4) ─────────────────────────────────────────────────────

def test_namespace_is_numeric_leaves_only():
    before = {"metrics": {"a": 1, "_diag": [1, 2], "nested": {"b": 2, "s": "x"}},
              "eval": {"engine": "hybrid", "r": 0.5}}
    ns = namespace(before, before)
    assert ns == ["eval.r", "metrics.a", "metrics.nested.b"]
    # underscore-prefixed AND non-numeric leaves are both excluded (§5.1.1)
    assert "metrics._diag" not in ns and "eval.engine" not in ns
    assert "metrics.nested.s" not in ns


def test_namespace_is_a_union_not_intersection():
    before = _b(a=1, b=2)
    after = _b(a=1)                                  # b dropped from after
    assert "metrics.b" in namespace(before, after)   # union keeps it


# ── ENVELOPE ─────────────────────────────────────────────────────────────────

def test_envelope_default_deny_catches_an_unintended_move():
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}]}
    # x hits its bound, but y moved and no clause covers it
    res = evaluate(ct, _b(x=830, y=100), _b(x=23, y=99))
    assert res["passed"] is False
    moved = [r for r in res["envelope"] if r["metric"] == "metrics.y"]
    assert moved and moved[0]["ok"] is False


def test_envelope_passes_when_only_intent_metrics_move():
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}]}
    assert evaluate(ct, _b(x=830, y=5), _b(x=23, y=5))["passed"] is True


def test_dropping_a_counter_raises_it_cannot_dodge_the_envelope():
    """§3.4 union semantics: a metric in before and absent from after is not
    silently unchanged — removing it is the very dodge default-deny closes."""
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}]}
    with pytest.raises(ContractError, match="absent from after"):
        evaluate(ct, _b(x=830, y=5), _b(x=23))       # y vanished


def test_a_same_metric_waiver_releases_exactly_one_metric():
    """The simple case: release `y` and bound `y` itself (omitting bound.metric)."""
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}],
          "envelope": {"mode": "default-deny",
                       "waivers": [{"release": "metrics.y",
                                    "bound": {"to": {"max": 200}},
                                    "reason": "expected to grow"}]}}
    # y moved 100 → 150, within the waiver's ceiling → PASS
    assert evaluate(ct, _b(x=830, y=100), _b(x=23, y=150))["passed"] is True
    # y moved past the ceiling → the waiver still gates
    assert evaluate(ct, _b(x=830, y=100), _b(x=23, y=250))["passed"] is False


def test_a_release_A_bound_B_waiver_expresses_the_fingerprint_shape():
    """§3.5: a hash string cannot carry a numeric bound, so a vault-mutating goal
    RELEASES `vault.content_fingerprint` and BOUNDS a sibling `changed_paths`
    count. This is the shape every G5-style goal needs, and the reason the waiver
    model separates release from bound."""
    before = {"metrics": {"x": 830},
              "vault": {"content_fingerprint": "aaa", "changed_paths": {"count": 0}}}
    after = {"metrics": {"x": 23},
             "vault": {"content_fingerprint": "bbb", "changed_paths": {"count": 12}}}
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}],
          "envelope": {"mode": "default-deny",
                       "waivers": [{"release": "vault.content_fingerprint",
                                    "bound": {"metric": "vault.changed_paths.count",
                                              "to": {"max": 30}},
                                    "reason": "wiki-link repair, graph/atomic only"}]}}
    assert evaluate(ct, before, after)["passed"] is True       # 12 ≤ 30
    over = {**after, "vault": {"content_fingerprint": "bbb",
                              "changed_paths": {"count": 400}}}
    assert evaluate(ct, before, over)["passed"] is False       # 400 > 30 → gated


def test_a_waiver_without_a_reason_raises():
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}],
          "envelope": {"mode": "default-deny",
                       "waivers": [{"release": "metrics.y", "bound": {"to": {"max": 9}}}]}}
    with pytest.raises(ContractError, match="no reason"):
        evaluate(ct, _b(x=830, y=1), _b(x=23, y=1))


def test_a_waiver_on_a_metric_outside_the_namespace_raises():
    """An inert waiver — almost always a typo — must be caught at the Contract
    stage, not silently ignored while the underlying metric stays default-denied."""
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}],
          "envelope": {"mode": "default-deny",
                       "waivers": [{"release": "metrics.TYPO",
                                    "bound": {"to": {"max": 9}}, "reason": "r"}]}}
    with pytest.raises(ContractError, match="not in the envelope namespace"):
        evaluate(ct, _b(x=830, y=1), _b(x=23, y=1))


def test_a_from_that_disagrees_with_the_before_raises():
    """`from` is an integrity check on the baseline, not decoration: a contract
    authored against a different before must not be graded."""
    ct = {"intent": [{"metric": "metrics.x", "from": 830, "to": {"max": 30}}]}
    with pytest.raises(ContractError, match="wrong baseline"):
        evaluate(ct, _b(x=500), _b(x=23))              # before says 500, not 830
    assert evaluate(ct, _b(x=830), _b(x=23))["passed"] is True   # matches → fine


def test_an_unknown_envelope_mode_raises():
    ct = {"intent": [], "envelope": {"mode": "allow-list"}}
    with pytest.raises(ContractError, match="default-deny"):
        evaluate(ct, _b(x=1), _b(x=1))


def test_empty_contract_over_a_still_vault_passes():
    """A no-op goal: nothing declared, nothing moved."""
    assert evaluate({"intent": []}, _b(x=1, y=2), _b(x=1, y=2))["passed"] is True


# ── supersedes shape (§3.3) ──────────────────────────────────────────────────

def test_supersedes_requires_a_matching_intent_bound():
    """A contract that releases an invariant it did not also bound is disabling
    a gate it never earned."""
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}],
          "supersedes": [{"invariant": "INV-4", "metric": "surfacing.visible",
                          "direction": "may-fall", "reason": "auto-pass narrowing"}]}
    # surfacing.visible is NOT an INTENT metric here → raise
    with pytest.raises(ContractError, match="no matching INTENT bound"):
        evaluate(ct, _b(x=830), _b(x=23))


def test_supersedes_with_a_matching_bound_is_accepted():
    ct = {"intent": [{"metric": "surfacing.visible", "to": {"min": 100}}],
          "supersedes": [{"invariant": "INV-4", "metric": "surfacing.visible",
                          "direction": "may-fall", "reason": "auto-pass narrowing"}]}
    before = {"metrics": {}, "surfacing": {"visible": 167}}
    after = {"metrics": {}, "surfacing": {"visible": 150}}
    assert evaluate(ct, before, after)["passed"] is True


def test_supersedes_needs_a_direction_and_a_reason():
    base = {"intent": [{"metric": "metrics.x", "to": {"max": 1}}]}
    for bad in ({"invariant": "INV-4", "metric": "metrics.x", "reason": "r"},
                {"invariant": "INV-4", "metric": "metrics.x",
                 "direction": "sideways", "reason": "r"},
                {"invariant": "INV-4", "metric": "metrics.x",
                 "direction": "may-fall"}):
        with pytest.raises(ContractError):
            evaluate({**base, "supersedes": [bad]}, _b(x=5), _b(x=1))
