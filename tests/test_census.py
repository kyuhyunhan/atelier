"""Node census (RFC 0006 P0.2): projection path and filesystem fallback agree.

Same discipline as `tests/test_projection_counts.py` — the count *semantics* live
in one place (`census._tally`), so the two data sources can only differ on node
*population*, which a reindex makes equal. The cold-DB case must fall back to disk
and return the same numbers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.service import api as _api
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import census as _census
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import review as _rev


def _capture_accept(seed: str, project: str = "lexio") -> None:
    cap = _cap.capture(observation=f"obs {seed}", why=f"why {seed}",
                       rule=f"rule {seed}",
                       working_dir=f"/Users/me/workspaces/{project}",
                       session_id=seed, hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"],
                target_topic="t", target_project=project)


def _reindex() -> None:
    _api.reindex(space="gorae", full=True)


def test_census_projection_matches_filesystem(atelier_env: Dict) -> None:
    _capture_accept("a"); _capture_accept("b")
    _reindex()
    vault = Path(_cl._vault_root())
    projected = _census.census()                       # warm DB → projection path
    from_disk = _census._tally(_census._fs_rows(vault))
    assert projected == from_disk                       # the two paths agree
    # sanity: the two accepted operational claims are counted as passed claims.
    assert projected["claim"]["ac_status"].get("passed") == 2
    assert projected["claim"]["domain"].get("operational") == 2


def test_cold_db_falls_back_to_filesystem(atelier_env: Dict) -> None:
    _capture_accept("a")
    # No reindex: the pages table is empty, so census() must read live disk.
    vault = Path(_cl._vault_root())
    got = _census.census()
    assert got == _census._tally(_census._fs_rows(vault))
    assert got["claim"]["ac_status"].get("passed") == 1


def test_census_partitions_by_kind(atelier_env: Dict) -> None:
    _capture_accept("a")
    _reindex()
    c = _census.census()
    # claim carries all three routing fields; a source/entity would only carry
    # its own. The shape is {kind: {field: {value: count}}}.
    assert set(c["claim"].keys()) == {"domain", "ac_status", "surfacing"}
