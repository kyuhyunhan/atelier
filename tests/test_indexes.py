"""PR-26: auto-generated INDEX.md for by-project and principles."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import indexes as _idx
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import review as _rev


def _accept(project: str, topic: str = "general", body_seed: str = "x") -> str:
    cap = _cap.capture(
        observation=f"obs {body_seed} for {project}",
        why=f"why {body_seed}", rule=f"rule {body_seed}",
        working_dir=f"/Users/me/workspaces/{project}",
        session_id=body_seed, hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem


# ── by-project INDEX.md ─────────────────────────────────────────────────


# RFC 0001 retired the by-project mirror and its per-project INDEX: accept /
# retract no longer auto-generate one (classification is a facet query, not a
# folder). The `regen_project` function and its full removal live in P7; only
# its graceful-absence behavior is still asserted (below).


# ── principles INDEX.md — RETIRED (RFC 0005 §7.1) ──────────────────────────
#
# In the v7 field model a learning's tier is the `surfacing` field, not a
# directory, so the generated principles/INDEX.md folder listing described a tree
# that no longer exists. The two old tests asserting that INDEX.md is generated /
# regenerated on add/archive are therefore obsolete and removed. Their behavior
# is replaced by the contract below: the generator is a documented no-op that
# writes NOTHING into any legacy directory.


def test_principles_index_generator_is_retired_noop(atelier_env: Dict) -> None:
    """regen_principles() is retired: it returns the legacy result shape with
    written=False and never writes a legacy learnings/principles/INDEX.md."""
    out = _idx.regen_principles()
    assert out["written"] is False
    assert out["count"] == 0
    assert "retired" in str(out.get("reason", "")).lower()
    # safe wrapper is callable and silent
    assert _idx.safe_regen_principles() is None


def test_principle_lifecycle_writes_no_legacy_index(atelier_env: Dict) -> None:
    """Adding/archiving a principle must not resurrect the legacy generated
    INDEX.md in any learnings/principles/ tree (field model has no such folder
    index)."""
    _pr.add(title="rule one", rule="r", why="w",
            priority="always-inject", slug="rule-one")
    _pr.archive(slug="rule-one", reason="x")
    legacy = atelier_env["gorae"] / "learnings" / "principles" / "INDEX.md"
    canonical = (atelier_env["gorae"] / "provenance" / "learning"
                 / "principles" / "INDEX.md")
    assert not legacy.exists()
    assert not canonical.exists()
