"""RFC 0006 Pillar ④a — forgetting is flag-only, reusing the surfacing audit."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.service import api as _api
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import lateral as _lat
from runtime.service.learnings import review as _rev
from runtime.service.learnings import store as _store
from runtime.service.learnings import surfacing as _surf


def _capture_accept(seed: str, project: str = "lexio") -> None:
    cap = _cap.capture(observation=f"obs {seed} about widgets", why=f"why {seed}",
                       rule=f"rule {seed}", working_dir=f"/Users/me/workspaces/{project}",
                       session_id=seed, hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"], target_topic="t", target_project=project)


def test_plan_forgets_is_a_pure_read(atelier_env: Dict) -> None:
    _capture_accept("a"); _capture_accept("b")
    vault = Path(_cl._vault_root())
    before = sum(1 for _ in _store.iter_accepted_files(vault))

    plan = _lat.plan_forgets()

    after = sum(1 for _ in _store.iter_accepted_files(vault))
    assert after == before                       # never mutates
    assert plan["total"] == before
    # accepted claims have real statements → visible → no dark candidates here
    assert plan["candidate_count"] == 0
    assert plan["candidates"] == []
    assert "human" in plan["note"]                # flag-only is documented, not implicit


def test_plan_forgets_uses_the_same_audit_as_the_omission_gate(atelier_env: Dict) -> None:
    """The forgetting candidate set and INV-4's dark_count must come from the
    SAME measurement — no second, drifting definition of 'forgettable'."""
    _capture_accept("a")
    plan = _lat.plan_forgets()
    aud = _surf.audit()
    assert plan["candidate_count"] == aud["dark_count"]
    assert plan["total"] == aud["total"]


def test_flagged_candidate_is_retractable_by_a_human(atelier_env: Dict) -> None:
    # A candidate's entry_id, once flagged, must resolve through review.retract —
    # the actual human-gated action plan_forgets defers to.
    _capture_accept("a")
    vault = Path(_cl._vault_root())
    claim = next(_store.iter_accepted_files(vault))
    from runtime.index import parse as _parse
    fm, _ = _parse.split_frontmatter(claim.read_text(encoding="utf-8"))
    eid = fm["entry_id"]

    out = _rev.retract(slug=eid, reason="test-forget")
    assert out["ac_status"] == "retracted"
    # retracted claims drop out of the accepted pool plan_forgets scans.
    assert eid not in [p.stem for p in _store.iter_accepted_files(vault)]
