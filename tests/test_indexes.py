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


# ── principles INDEX.md ──────────────────────────────────────────────────


def test_principle_add_creates_principles_index(atelier_env: Dict) -> None:
    _pr.add(title="rule one", rule="r", why="w",
             priority="always-inject", slug="rule-one")
    idx = (atelier_env["gorae"] / "learnings" / "principles" / "INDEX.md")
    assert idx.exists()
    text = idx.read_text()
    assert "## always-inject" in text
    assert "rule one" in text


def test_principle_archive_regens_index(atelier_env: Dict) -> None:
    _pr.add(title="a", rule="r", why="w",
             priority="always-inject", slug="a")
    _pr.add(title="b", rule="r2", why="w2",
             priority="always-inject", slug="b")
    idx = (atelier_env["gorae"] / "learnings" / "principles" / "INDEX.md")
    assert "entry_count: 2" in idx.read_text()
    _pr.archive(slug="a", reason="x")
    assert "entry_count: 1" in idx.read_text()


# ── direct regen API ─────────────────────────────────────────────────────


def test_regen_project_missing_dir_is_safe(atelier_env: Dict) -> None:
    out = _idx.regen_project("nonexistent-project")
    assert out["written"] is False
    assert out["count"] == 0
