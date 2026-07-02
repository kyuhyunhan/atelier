"""Nudge counts read from the DB projection (fast path) with a filesystem
fallback on a cold/un-reindexed DB.

The count *semantics* live in one place (the predicate helpers); these tests
lock in that the projection path and the filesystem path agree, and that the
fallback fires when the projection can't answer.
"""
from __future__ import annotations

from typing import Dict

from runtime.service import api as _api
from runtime.service.learnings import atomize as _atomize
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import projection_counts as _pc
from runtime.service.learnings import review as _rev
from runtime.promote import propose as _propose


def _capture_accept(seed: str, project: str = "lexio") -> None:
    """Born-as-claim capture, then pass the acceptance gate — the claim lands in
    graph/atomic at ac_status:passed, surfacing:query."""
    cap = _cap.capture(observation=f"obs {seed}", why=f"why {seed}",
                       rule=f"rule {seed}",
                       working_dir=f"/Users/me/workspaces/{project}",
                       session_id=seed, hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"],
                target_topic="t", target_project=project)


def _reindex() -> None:
    _api.reindex(space="gorae", full=True)


def _fs_unatomized(vault) -> int:
    sources = _atomize._source_ids(vault)
    if not sources:
        return 0
    atomized = _atomize._atomized_source_ids(vault) & sources
    return len(sources - atomized)


# ── projection path: parity with the filesystem after a reindex ──────────────


def test_accepted_count_projection_matches_filesystem(atelier_env: Dict) -> None:
    _capture_accept("a"); _capture_accept("b")
    _reindex()
    vault = _cl._vault_root()
    projected = _pc.accepted_operational()
    assert projected == 2                              # projection path is live
    assert projected == _cl._count_accepted(vault)      # and agrees with the scan


def test_promote_eligible_projection_matches_filesystem(atelier_env: Dict) -> None:
    # accepted claims are surfacing:query + ac_status:passed → promote-eligible.
    _capture_accept("a"); _capture_accept("b")
    _reindex()
    projected = _pc.promote_eligible(limit=50)
    assert projected == 2
    assert projected == len(_propose._eligible(limit=50))


def test_unatomized_projection_matches_filesystem(atelier_env: Dict) -> None:
    _capture_accept("a")
    _reindex()
    vault = _cl._vault_root()
    projected = _pc.unatomized_sources()
    assert projected is not None
    assert projected == _fs_unatomized(vault)


# ── cold DB: projection can't answer → callers fall back to the scan ─────────


def test_cold_db_returns_none_and_caller_falls_back(atelier_env: Dict) -> None:
    _capture_accept("a"); _capture_accept("b")
    # No reindex: the pages table has no claim/source rows.
    assert _pc.accepted_operational() is None          # projection abstains
    # dream_status still returns the correct total via the filesystem fallback.
    assert _cl.dream_status()["accepted_total"] == 2
