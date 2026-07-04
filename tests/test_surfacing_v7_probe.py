"""RFC 0006 P0.2b — the surfacing audit must not be blind on v7 claims.

A v7 accepted claim carries `statement` but none of the pre-v7 concept fields
(`touches`/`target_topic`/`title`). Before the fix, `_concept_probe` returned an
empty string for every such claim, so `snapshot()` marked them all dark by
construction and `eval._self_probe_block` counted 0 probes — silently disabling
the omission gate (INV-4). These tests lock the `statement` fallback in.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.service import api as _api
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import eval as _eval
from runtime.service.learnings import review as _rev
from runtime.service.learnings import surfacing as _surf


def _capture_accept(seed: str, project: str = "lexio") -> None:
    cap = _cap.capture(observation=f"observation about {seed} indexing throughput",
                       why=f"why {seed}", rule=f"rule {seed}",
                       working_dir=f"/Users/me/workspaces/{project}",
                       session_id=seed, hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"],
                target_topic="t", target_project=project)


def test_concept_probe_falls_back_to_statement() -> None:
    # No touches/target_topic/title — only a v7 statement.
    probe = _surf._concept_probe({"kind": "claim", "statement": "batch-write API throughput"})
    assert probe.strip()                                 # not empty …
    assert "batch" in probe and "throughput" in probe    # … and derived from statement


def test_v7_accepted_claim_is_probeable_and_visible(atelier_env: Dict) -> None:
    _capture_accept("alpha")
    _api.reindex(space="gorae", full=True)

    snap = _surf.snapshot()
    assert snap, "expected the accepted v7 claim in the snapshot"
    # every enumerated claim now has a non-empty probe (no dark-by-construction)
    assert all((s["probe"] or "").strip() for s in snap.values())

    aud = _surf.audit()
    assert aud["total"] >= 1
    assert aud["visible"] >= 1               # findable by its own statement …
    assert aud["dark_count"] == 0            # … so nothing is dark

    # eval's self-probe block now actually runs (was 0 probes when blind).
    sp = _eval._self_probe_block(5)
    assert sp["probes"] == aud["total"]
