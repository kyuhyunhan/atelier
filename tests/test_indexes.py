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


def test_accept_creates_project_index(atelier_env: Dict) -> None:
    _accept("lexio", topic="db-tests", body_seed="a")
    idx = (atelier_env["gorae"] / "learnings" / "accepted"
           / "by-project" / "lexio" / "INDEX.md")
    assert idx.exists()
    text = idx.read_text()
    assert "atelier:generated" in text
    assert "## db-tests" in text


def test_index_groups_by_topic(atelier_env: Dict) -> None:
    _accept("lexio", topic="db-tests", body_seed="a")
    _accept("lexio", topic="rendering", body_seed="b")
    idx = (atelier_env["gorae"] / "learnings" / "accepted"
           / "by-project" / "lexio" / "INDEX.md")
    text = idx.read_text()
    assert "## db-tests" in text
    assert "## rendering" in text
    assert "entry_count: 2" in text


def test_retract_regens_indexes(atelier_env: Dict) -> None:
    slug = _accept("lexio", topic="db-tests", body_seed="a")
    _accept("lexio", topic="rendering", body_seed="b")
    idx = (atelier_env["gorae"] / "learnings" / "accepted"
           / "by-project" / "lexio" / "INDEX.md")
    assert "entry_count: 2" in idx.read_text()
    _rev.retract(slug=slug, reason="oops")
    assert "entry_count: 1" in idx.read_text()


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


def test_regen_idempotent(atelier_env: Dict) -> None:
    # accept already auto-regens, so the first explicit regen finds
    # nothing to change.
    _accept("lexio", topic="db-tests", body_seed="a")
    r1 = _idx.regen_project("lexio")
    assert r1["written"] is False    # same content as auto-regen wrote
    # Force a change in the directory and regen — should rewrite.
    extra = (atelier_env["gorae"] / "learnings" / "accepted"
             / "by-project" / "lexio" / "extra.md")
    extra.write_text("---\nschema_version: 4\nentry_id: ex\n"
                     "title: extra\ntarget_topic: misc\n---\n## Rule\nx\n")
    r2 = _idx.regen_project("lexio")
    assert r2["written"] is True
    assert r2["count"] == 2


def test_regen_project_missing_dir_is_safe(atelier_env: Dict) -> None:
    out = _idx.regen_project("nonexistent-project")
    assert out["written"] is False
    assert out["count"] == 0
