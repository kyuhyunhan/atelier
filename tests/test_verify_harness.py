"""RFC 0006 P0.3 — the independent verifier.

An unchanged vault must PASS (baseline == after ⇒ no regression). A baseline that
is not frozen (uncommitted / outside git) must be refused. A real regression
(data loss) must FAIL a gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from runtime.service import api as _api
from runtime.service.learnings import baseline as _baseline
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import review as _rev
from runtime.service.learnings import verify as _verify


def _capture_accept(seed: str, project: str = "lexio") -> None:
    cap = _cap.capture(observation=f"observation {seed} about throughput",
                       why=f"why {seed}", rule=f"rule {seed}",
                       working_dir=f"/Users/me/workspaces/{project}",
                       session_id=seed, hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"],
                target_topic="t", target_project=project)


def _freeze(tmp_path: Path) -> Path:
    _api.reindex(space="gorae", full=True)
    bp = tmp_path / "baseline.json"
    _baseline.write(bp)
    return bp


def test_verifier_passes_on_unchanged_vault(atelier_env: Dict, tmp_path: Path) -> None:
    _capture_accept("a"); _capture_accept("b")
    bp = _freeze(tmp_path)
    # No change between freeze and verify → every no-regression gate holds.
    report = _verify.verify_against(bp, "P0", require_committed=False)
    assert report["passed"] is True
    assert all(c["ok"] for c in report["checks"] if c["severity"] == "gate")


def test_verifier_refuses_non_frozen_baseline(atelier_env: Dict, tmp_path: Path) -> None:
    _capture_accept("a")
    bp = _freeze(tmp_path)
    # tmp_path is not a git repo → the frozen-baseline guard fails closed.
    with pytest.raises(RuntimeError):
        _verify.verify_against(bp, "P0")            # require_committed defaults True


def test_verifier_fails_on_data_loss(atelier_env: Dict, tmp_path: Path) -> None:
    _capture_accept("a"); _capture_accept("b")
    bp = _freeze(tmp_path)

    # Simulate loss: delete every accepted claim file, then reindex so the
    # projection reflects the smaller vault.
    from runtime.service.learnings import store as _store
    vault = Path(_cl._vault_root())
    for p in list(_store.iter_accepted_files(vault)):
        p.unlink()
    _api.reindex(space="gorae", full=True)

    report = _verify.verify_against(bp, "P0", require_committed=False)
    assert report["passed"] is False
    dl = next(c for c in report["checks"] if c["name"] == "no_data_loss")
    assert dl["ok"] is False


def test_unknown_rubric_raises(atelier_env: Dict, tmp_path: Path) -> None:
    _capture_accept("a")
    bp = _freeze(tmp_path)
    with pytest.raises(KeyError):
        _verify.verify_against(bp, "does-not-exist", require_committed=False)


def test_p1_grounded_rubric(atelier_env: Dict, tmp_path: Path) -> None:
    from runtime.structure import manifest as _manifest
    _capture_accept("a")
    bp = _freeze(tmp_path)
    vault = Path(_cl._vault_root())

    # Without a manifest, the P1 manifest gate fails (lens_coverage still ok).
    r1 = _verify.verify_against(bp, "P1_grounded", require_committed=False)
    assert r1["passed"] is False
    assert next(c for c in r1["checks"] if c["name"] == "manifest")["ok"] is False
    assert next(c for c in r1["checks"] if c["name"] == "lens_coverage")["ok"] is True

    # After grounding the vault, P1 passes.
    _manifest.ensure(vault)
    r2 = _verify.verify_against(bp, "P1_grounded", require_committed=False)
    assert r2["passed"] is True
