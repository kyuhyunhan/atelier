"""PR-28: signal-detector recall."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import recall as _rc
from runtime.service.learnings import review as _rev


def _accept(observation: str, why: str, rule: str,
            project: str, topic: str = "general") -> str:
    cap = _cap.capture(
        observation=observation, why=why, rule=rule,
        working_dir=f"/Users/me/workspaces/{project}",
        session_id=project, hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem


# ── basic retrieval (FS fallback path; FTS may not have entries) ──────────


def test_recall_returns_matching_principle(atelier_env: Dict) -> None:
    _pr.add(title="prefer real db",
             rule="integration tests must hit a real database, not mocks.",
             why="mocked tests diverge from prod schema.",
             priority="always-inject",
             slug="prefer-real-db")
    out = _rc.recall(query="mocked database tests", top_k=5)
    assert out["count"] >= 1
    slugs = {it["slug"] for it in out["items"]}
    assert "prefer-real-db" in slugs


def test_recall_returns_matching_accepted_learning(atelier_env: Dict) -> None:
    _accept("react children re-render twice with batching",
             "useTransition needs keys", "stabilize children keys",
             project="bht", topic="rendering")
    out = _rc.recall(query="react children re-render", top_k=5)
    assert out["count"] >= 1
    items = out["items"]
    assert any("react" in (it["snippet"] + it["title"]).lower() for it in items)


# ── project boost ─────────────────────────────────────────────────────────


def test_recall_boosts_current_project(atelier_env: Dict) -> None:
    _accept("foo render flicker", "render bug", "use memo",
             project="lexio", topic="rendering")
    _accept("foo render flicker", "render bug elsewhere", "use memo also",
             project="bht", topic="rendering")
    out = _rc.recall(query="render flicker", project="lexio", top_k=2)
    # Both rendering hits match; lexio (current project) should come first.
    assert out["count"] >= 1
    assert out["items"][0]["project"] == "lexio"


# ── markdown rendering ────────────────────────────────────────────────────


def test_recall_renders_markdown_block(atelier_env: Dict) -> None:
    _pr.add(title="t", rule="x", why="y", priority="always-inject",
             slug="t1")
    out = _rc.recall(query="x", top_k=1, max_chars=500)
    md = out["markdown"]
    assert md.startswith("## atelier — relevant memory")
    assert "[principle]" in md


def test_recall_empty_query_returns_no_items(atelier_env: Dict) -> None:
    _pr.add(title="t", rule="x", why="y", priority="always-inject",
             slug="t1")
    out = _rc.recall(query="", top_k=5)
    assert out["count"] == 0
    assert out["markdown"] == ""


# ── MCP dispatch ──────────────────────────────────────────────────────────


def test_mcp_dispatch_recall(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _accept("react flicker", "x", "y", project="lexio", topic="rendering")

    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_recall",
            query="react flicker",
            project="lexio",
            top_k=3,
        )
    out = asyncio.run(go())
    assert out["count"] >= 1


def test_recall_falls_back_to_working_dir_for_project(atelier_env: Dict) -> None:
    from runtime.service import auth, tools as _tools
    _accept("react flicker", "x", "y", project="lexio", topic="rendering")
    sess = auth.Session(
        agent_kind="claude-code", transport="mcp-http",
        working_dir="/Users/me/workspaces/lexio",
        caller="test", claims=frozenset(),
    )
    tok = _tools.set_session(sess)
    try:
        async def go() -> Dict:
            return await _tools.invoke("atelier_recall", query="react")
        out = asyncio.run(go())
    finally:
        _tools._current.reset(tok)
    assert out["project"] == "lexio"


# ── recall quality: dedup + generated-file exclusion ───────────────────────


def test_is_noise_excludes_generated_projections() -> None:
    assert _rc._is_noise("learnings/accepted/by-project/frontend/INDEX.md")
    assert _rc._is_noise("learnings/accepted/by-topic/general/TAXONOMY.md")
    assert _rc._is_noise("INDEX")          # fs-scan bare-stem slug
    assert _rc._is_noise("TAXONOMY")
    assert not _rc._is_noise("learnings/accepted/by-topic/x/claude-foo.md")
    assert not _rc._is_noise("claude-foo")


def test_dedup_by_entry_id_keeps_best_ranked_and_passes_eidless() -> None:
    hits = [
        {"slug": "by-topic/a.md",   "fm": {"entry_id": "E1"}, "score": -2.0},
        {"slug": "by-project/a.md", "fm": {"entry_id": "E1"}, "score": -1.0},
        {"slug": "by-topic/b.md",   "fm": {"entry_id": "E2"}, "score": -1.5},
        {"slug": "no-eid.md",       "fm": {},                 "score": -0.5},
    ]
    out = _rc._dedup_by_entry_id(hits)
    assert [h["slug"] for h in out] == ["by-topic/a.md", "by-topic/b.md", "no-eid.md"]


def test_recall_collapses_canonical_and_mirror_copies(atelier_env: Dict) -> None:
    """End-to-end through FTS: one accepted learning is stored as a by-topic
    canonical AND a by-project mirror (same entry_id). After indexing both,
    recall must return it exactly once."""
    from runtime.service import api
    _accept("zqxwv flicker phenomenon on mount", "why it matters",
             "stabilize keys", project="bht", topic="rendering")
    api.reindex(space="gorae", full=True)          # index both on-disk copies
    out = _rc.recall(query="zqxwv flicker", top_k=5)
    assert out["count"] == 1                       # was 2 before dedup


def test_recall_excludes_generated_files_from_fts(atelier_env: Dict) -> None:
    from runtime.service import api
    _accept("zqxwv flicker phenomenon on mount", "why it matters",
             "stabilize keys", project="bht", topic="rendering")
    api.reindex(space="gorae", full=True)          # also indexes by-project INDEX.md
    out = _rc.recall(query="zqxwv flicker", top_k=10)
    blob = " ".join(it["slug"] for it in out["items"])
    assert "INDEX" not in blob
    assert "TAXONOMY" not in blob


# ── inject-preview CLI ─────────────────────────────────────────────────────


def test_inject_preview_cli_renders_bootstrap_and_recall(
        atelier_env: Dict, capsys: pytest.CaptureFixture) -> None:
    from runtime import cli
    _accept("foo render flicker", "render bug", "use memo",
             project="lexio", topic="rendering")
    rc = cli.main(["inject-preview", "--cwd", "/Users/me/workspaces/lexio",
                   "--query", "render flicker"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "project='lexio'" in out
    assert "source=basename" in out
    assert "session-start bootstrap" in out
    assert "per-turn recall" in out
    assert "relevant memory" in out


def test_recall_concept_overlap_boosts(atelier_env):
    """A learning whose `touches` concept appears in the query gets boosted even
    when its body barely matches lexically — the concept-index retrieval payoff."""
    from runtime.service.learnings import recall as _rc
    fm = {"touches": ["dependency-direction"], "target_topic": "architecture"}
    base = _rc._boost({"score": 0.0, "fm": {}, "page_type": "learning_accepted"},
                      None, frozenset({"dependency"}))
    boosted = _rc._boost({"score": 0.0, "fm": fm, "page_type": "learning_accepted"},
                         None, frozenset({"dependency"}))
    assert boosted < base   # lower score = ranked higher
