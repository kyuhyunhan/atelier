"""RFC 0009 §6 / §8.1 — the goal orchestrator's pure core.

`verify_contract(contract, before, after)` is pure, so the three-layer scoring —
INTENT, ENVELOPE, and the schema-driven invariants with supersession applied — is
tested here against synthetic baselines. The end-to-end path (freeze + a real
vault) is `test_goal_run.py`.
"""
from __future__ import annotations

import pytest

from runtime.service.learnings import goal as _goal
from runtime.service.learnings.contract import ContractError


def _base(**kw):
    """A baseline with the blocks the layers read. `census` carries one kind so
    INV-1 has something to check; override any block via kwargs."""
    b = {"metrics": {}, "census": {"claim": {"domain": {"knowledge": 10}}},
         "surfacing": {"visible": 100, "dark_count": 0},
         "eval": {"self_probe": {"recall_at_k": 1.0},
                  "paraphrase": {"recall_at_k": 0.6}},
         "vault": {"content_fingerprint": "same"}}
    b.update(kw)
    return b


# ── the three layers together ────────────────────────────────────────────────

def test_a_clean_reduction_passes_all_three_layers():
    before = _base(metrics={"promote_eligible": {"total": 830}})
    after = _base(metrics={"promote_eligible": {"total": 23}})
    ct = {"intent": [{"metric": "metrics.promote_eligible.total",
                      "from": 830, "to": {"max": 30}}]}
    res = _goal.verify_contract(ct, before, after)
    assert res["passed"] is True
    assert all(c["ok"] for c in res["invariants"])


def test_an_invariant_regression_fails_even_when_intent_holds():
    """The delta axis sits BESIDE the invariants, it does not replace them: a
    goal that hits its bound but drops recall still FAILs."""
    before = _base(metrics={"x": 830})
    after = _base(metrics={"x": 23},
                  eval={"self_probe": {"recall_at_k": 0.5},   # regressed
                        "paraphrase": {"recall_at_k": 0.6}})
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}]}
    res = _goal.verify_contract(ct, before, after)
    assert res["passed"] is False
    bad = [c for c in res["invariants"] if not c["ok"]]
    assert bad and bad[0]["id"] == "eval/self_probe.recall_at_k"


def test_an_unintended_vault_change_fails_the_envelope():
    """§5.7: hitting the metric bound while rewriting unrelated files must not
    pass — the fingerprint moved and no waiver covers it."""
    before = _base(metrics={"x": 830})
    after = _base(metrics={"x": 23}, vault={"content_fingerprint": "DIFFERENT"})
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}]}
    res = _goal.verify_contract(ct, before, after)
    assert res["passed"] is False
    fp = [c for c in res["envelope"] if c["metric"] == "vault.content_fingerprint"]
    assert fp and fp[0]["ok"] is False


# ── supersession (§3.3) ──────────────────────────────────────────────────────

def test_supersedes_releases_exactly_the_named_clause():
    """G5's shape: auto-pass narrowing shrinks the accepted pool, so `visible`
    falls by design. The contract releases INV-4/visible with a matching INTENT
    bound — and `dark_count` stays guarded."""
    before = _base(surfacing={"visible": 167, "dark_count": 0})
    after = _base(surfacing={"visible": 150, "dark_count": 0})
    ct = {"intent": [{"metric": "surfacing.visible", "to": {"min": 100}}],
          "supersedes": [{"invariant": "INV-4", "metric": "surfacing.visible",
                          "direction": "may-fall",
                          "reason": "auto-pass narrowing"}]}
    res = _goal.verify_contract(ct, before, after)
    assert res["passed"] is True
    released = [c for c in res["invariants"] if c.get("superseded")]
    assert released and released[0]["id"] == "INV-4/surfacing.visible"


def test_supersedes_does_not_release_the_sibling_clause():
    """Per-clause, not per-invariant: releasing `visible` must NOT also stop
    gating `dark_count`, the exact coarseness §3.3 warns against."""
    before = _base(surfacing={"visible": 167, "dark_count": 0})
    after = _base(surfacing={"visible": 150, "dark_count": 5})   # dark_count ROSE
    ct = {"intent": [{"metric": "surfacing.visible", "to": {"min": 100}}],
          "supersedes": [{"invariant": "INV-4", "metric": "surfacing.visible",
                          "direction": "may-fall", "reason": "auto-pass narrowing"}]}
    res = _goal.verify_contract(ct, before, after)
    assert res["passed"] is False
    bad = [c for c in res["invariants"] if not c["ok"]]
    assert bad and bad[0]["id"] == "INV-4/surfacing.dark_count"


def test_supersedes_a_clause_that_is_not_a_known_invariant_raises():
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 1}}],
          "supersedes": [{"invariant": "INV-9", "metric": "metrics.x",
                          "direction": "may-fall", "reason": "r"}]}
    with pytest.raises(ContractError, match="not\\s+a known invariant clause"):
        _goal.verify_contract(ct, _base(metrics={"x": 5}), _base(metrics={"x": 1}))


# ── the fingerprint waiver, end-to-end through the core (§3.5) ────────────────

def test_fingerprint_waiver_bounds_changed_paths_from_the_digest_maps():
    """A vault-mutating goal releases the fingerprint and bounds the count of
    changed files. The core computes that count by diffing the per-file digest
    maps the round baseline carries under `_file_digests`."""
    before = _base(metrics={"x": 830},
                   vault={"content_fingerprint": "before"})
    before["_file_digests"] = {"a.md": "h1", "b.md": "h2", "c.md": "h3"}
    after = _base(metrics={"x": 23}, vault={"content_fingerprint": "after"})
    after["_file_digests"] = {"a.md": "h1", "b.md": "CHANGED", "c.md": "h3"}
    ct = {"intent": [{"metric": "metrics.x", "to": {"max": 30}}],
          "envelope": {"mode": "default-deny",
                       "waivers": [{"release": "vault.content_fingerprint",
                                    "bound": {"metric": "vault.changed_paths.count",
                                              "to": {"max": 5}},
                                    "reason": "wiki-link repair"}]}}
    res = _goal.verify_contract(ct, before, after)
    assert res["passed"] is True                     # 1 file changed ≤ 5

    # rewrite everything → count 3, still ≤ 5? make the bound tighter
    ct["envelope"]["waivers"][0]["bound"]["to"] = {"max": 0}
    res2 = _goal.verify_contract(ct, before, after)
    assert res2["passed"] is False                   # 1 changed > 0 → gated


def test_no_data_loss_stays_whole_and_unreleasable():
    """INV-1 is not in the schema clause list and cannot be superseded — a goal
    never legitimately reduces a node kind."""
    before = _base(census={"claim": {"domain": {"knowledge": 10}}})
    after = _base(census={"claim": {"domain": {"knowledge": 3}}})   # 10 → 3
    ct = {"intent": []}
    res = _goal.verify_contract(ct, before, after)
    assert res["passed"] is False
    inv1 = [c for c in res["invariants"] if c["id"] == "INV-1/no_data_loss"]
    assert inv1 and inv1[0]["ok"] is False


# ── review round: the tolerances and the typed abort ─────────────────────────

def test_eval_metrics_tolerate_float_noise_on_an_unchanged_run():
    """§8.1 side one: an unchanged vault must PASS. Eval recall is not bit-stable
    run-to-run, so envelope equality and the decomposed invariants carry the same
    _EPS the reused gate has — a sub-epsilon wobble must not FAIL a no-op."""
    before = _base(eval={"self_probe": {"recall_at_k": 1.0},
                         "paraphrase": {"recall_at_k": 0.6363636363636364}})
    after = _base(eval={"self_probe": {"recall_at_k": 1.0},
                        "paraphrase": {"recall_at_k": 0.6363636363636365}})  # +1 ULP
    res = _goal.verify_contract({"intent": []}, before, after)
    assert res["passed"] is True


def test_a_real_eval_regression_still_fails():
    """The tolerance must not swallow a genuine drop."""
    before = _base(eval={"self_probe": {"recall_at_k": 1.0},
                         "paraphrase": {"recall_at_k": 0.6}})
    after = _base(eval={"self_probe": {"recall_at_k": 0.9},   # real regression
                        "paraphrase": {"recall_at_k": 0.6}})
    res = _goal.verify_contract({"intent": []}, before, after)
    assert res["passed"] is False


def test_a_broken_invariant_map_raises_contract_error(monkeypatch):
    """§6: an unreadable invariant map is an untrustworthy-harness condition and
    must surface as the typed hard-abort a caller catches, not a raw yaml error."""
    from pathlib import Path as _P
    monkeypatch.setattr(_goal, "_INVARIANTS_YAML", _P("/nonexistent.yaml"))
    with pytest.raises(ContractError, match="cannot read the invariant map"):
        _goal.verify_contract({"intent": []}, _base(), _base())


def test_verify_contract_does_not_mutate_its_after_argument():
    """Documented pure: the caller's `after` dict must come back unchanged even
    though a fingerprint waiver needs `vault.changed_paths` injected."""
    before = _base()
    before["_file_digests"] = {"a.md": "h1"}
    after = _base()
    after["_file_digests"] = {"a.md": "CHANGED"}
    import copy
    snapshot = copy.deepcopy(after)
    _goal.verify_contract({"intent": []}, before, after)
    assert after == snapshot                          # no in-place edit
