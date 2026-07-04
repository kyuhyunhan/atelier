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


def _capture_accept(seed: str, project: str = "lexio", topic: str = "t") -> None:
    cap = _cap.capture(observation=f"obs {seed} about widgets", why=f"why {seed}",
                       rule=f"rule {seed}", working_dir=f"/Users/me/workspaces/{project}",
                       session_id=seed, hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"], target_topic=topic, target_project=project)


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


def test_flagged_dark_candidate_is_retracted_end_to_end(
        atelier_env: Dict, monkeypatch) -> None:
    """Drive the FULL loop this pillar exists to enable: a genuinely dark
    learning appears in plan_forgets()'s candidates (with a real, human-readable
    `slug` — not entry_id duplicated), gets retracted using that candidate's own
    `slug` field, and is then gone from BOTH the accepted pool and a fresh
    plan_forgets() call."""
    _capture_accept("visible", topic="topic-visible")   # a normal, findable one …
    _capture_accept("darkone", topic="topic-darkone")    # … and one forced dark

    vault = Path(_cl._vault_root())
    from runtime.index import parse as _parse
    dark_eid = None
    for p in _store.iter_accepted_files(vault):
        fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        if "darkone" in str(fm.get("statement") or ""):
            dark_eid = fm["entry_id"]
    assert dark_eid is not None

    # Force exactly this one claim's own concept-probe (its target_topic,
    # "topic-darkone") to return no hits; everything else recalls normally.
    from runtime.service.learnings import recall as _recall_mod
    real_rank_hits = _recall_mod.rank_hits
    def _fake_rank_hits(query, project, types, *, top_k, vault=None):
        # concept_tokens splits "topic-darkone" on "-" into "topic darkone".
        if "darkone" in query.split():
            return []
        return real_rank_hits(query, project, types, top_k=top_k, vault=vault)
    monkeypatch.setattr(
        "runtime.service.learnings.surfacing._recall.rank_hits", _fake_rank_hits)

    plan = _lat.plan_forgets()
    dark_ids = {c["entry_id"] for c in plan["candidates"]}
    assert dark_eid in dark_ids
    candidate = next(c for c in plan["candidates"] if c["entry_id"] == dark_eid)
    assert candidate["slug"] != candidate["entry_id"]   # a REAL slug, not a dup

    out = _rev.retract(slug=candidate["slug"], reason="test-forget")
    assert out["ac_status"] == "retracted"

    # gone from the accepted pool …
    remaining_ids = []
    for p in _store.iter_accepted_files(vault):
        fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        remaining_ids.append(fm["entry_id"])
    assert dark_eid not in remaining_ids

    # … and gone from a fresh plan_forgets() call.
    plan2 = _lat.plan_forgets()
    assert dark_eid not in {c["entry_id"] for c in plan2["candidates"]}
