"""PR-22: learning_search + learning_relink."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import review as _rev
from runtime.service.learnings import search as _ls


def _accept_one(project: str = "lexio",
                topic: str = "search-fallback") -> Path:
    cap = _cap.capture(
        observation="search returns nothing for tilde queries",
        why="fts5 ignores tilde tokens; need fallback",
        rule="enable fallback for punctuation in queries",
        working_dir=f"/Users/me/workspaces/{project}",
        session_id="abc",
        hook="Stop",
    )
    res = _rev.accept(candidate_slug=cap["entry_id"],
                      target_topic=topic, target_project=project)
    return Path(res["path"])


def test_search_accepted_default(atelier_env: Dict) -> None:
    _accept_one()
    out = _ls.search(query="tilde")
    assert out["count"] == 1
    assert out["items"][0]["topic"] == "search-fallback"
    assert out["items"][0]["project"] == "lexio"


def test_search_sanitizes_punctuated_query(atelier_env: Dict) -> None:
    """A hyphenated/operator query must not crash FTS MATCH and silently fall to
    the grep scan — search() sanitizes like fts.search/recall do."""
    from runtime.service import api
    cap = _cap.capture(
        observation="the session-end auto-commit safety net catches the skip",
        why="phase-advance could skip the commit", rule="commit at session end",
        working_dir="/Users/me/workspaces/lexio", session_id="z", hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"], target_topic="cross-cutting",
                target_project="lexio")
    api.reindex(full=True)
    # Would previously raise OperationalError → empty FTS → degraded grep path.
    out = _ls.search(query="session-end auto-commit: safety-net!")
    assert out["count"] == 1
    assert out["items"][0]["project"] == "lexio"


def test_grep_fallback_facet_is_case_insensitive(atelier_env: Dict) -> None:
    """The DB-absent grep fallback must filter facets case-insensitively, exactly
    like the DB path — no silent mismatch (round-2 regression guard)."""
    vault = atelier_env["gorae"]
    # write directly + do NOT reindex → forces the grep fallback (no FTS rows)
    p = vault / "learnings" / "notes" / "2026-01" / "g.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nschema_version: 5\nentry_id: g\nstatus: accepted\n"
                 "ac_status: passed\nobservation_kind: project\n"
                 "captured_at: '2026-01-01T00:00:00Z'\n"
                 "accepted_at: '2026-01-02T00:00:00Z'\nagent_kind: claude-code\n"
                 "target_project: Lexio\naspect:\n- Cross-Cutting\n---\nbody words\n")
    out = _ls.search(query="", project="lexio", aspect="cross-cutting")
    assert out["count"] == 1 and out["items"][0]["entry_id"] == "g"


def test_search_filters_by_project(atelier_env: Dict) -> None:
    _accept_one(project="lexio")
    _accept_one(project="bht")
    out = _ls.search(query="", project="bht")
    assert out["count"] == 1
    assert out["items"][0]["project"] == "bht"


def test_search_includes_candidates_when_requested(atelier_env: Dict) -> None:
    _cap.capture(observation="raw candidate text here", hook="Stop",
                 require_why=False)
    out_accepted = _ls.search(query="raw", status="accepted")
    out_candidates = _ls.search(query="raw", status="candidate")
    assert out_accepted["count"] == 0
    assert out_candidates["count"] == 1


def test_relink_replaces_links(atelier_env: Dict) -> None:
    accepted = _accept_one()
    out = _ls.relink(slug=accepted.stem, links=["wiki/entities/fts5"])
    assert out["links"] == ["wiki/entities/fts5"]
    # One flat note, no mirror (RFC 0001): the change lands in the note itself.
    assert "wiki/entities/fts5" in accepted.read_text(encoding="utf-8")


def test_relink_merge_preserves_existing(atelier_env: Dict) -> None:
    accepted = _accept_one()
    _ls.relink(slug=accepted.stem, links=["wiki/entities/fts5"])
    out = _ls.relink(slug=accepted.stem,
                     links=["wiki/themes/search"], mode="merge")
    assert "wiki/entities/fts5" in out["links"]
    assert "wiki/themes/search" in out["links"]


def test_mcp_tools_registered_search_relink() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    assert "atelier_learning_search" in names
    assert "atelier_learning_relink" in names


def test_mcp_search_dispatch(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _accept_one()
    async def go() -> Dict:
        return await _tools.invoke("atelier_learning_search", query="tilde")
    out = asyncio.run(go())
    assert out["count"] == 1
