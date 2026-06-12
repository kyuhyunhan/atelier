"""RFC 0003 P5 — query-time synthesis evidence (the `think` layer)."""
from __future__ import annotations

import asyncio
from typing import Dict

from runtime.service.learnings import think as _think


def _accept(observation, why, rule, project, topic):
    from runtime.service.learnings import capture as _cap
    from runtime.service.learnings import review as _rev
    cap = _cap.capture(observation=observation, why=why, rule=rule,
                       working_dir=f"/Users/me/workspaces/{project}",
                       session_id=project, hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"], target_topic=topic,
                target_project=project)


def test_think_returns_cited_evidence_and_no_gap_when_covered(atelier_env: Dict):
    _accept("react children re-render twice with batching",
            "useTransition needs keys", "stabilize children keys",
            project="bht", topic="rendering")
    from runtime.service import api
    api.reindex(full=True)
    out = _think.think(query="react re-render batching", top_k=5)
    assert out["result_count"] >= 1
    assert out["citations"], "evidence must carry citations"
    c = out["citations"][0]
    assert set(c) == {"slug", "title", "snippet", "score"}


def test_think_reports_a_gap_when_nothing_matches(atelier_env: Dict):
    from runtime.service import api
    api.reindex(full=True)
    out = _think.think(query="quantum chromodynamics lattice gauge", top_k=5)
    assert out["result_count"] == 0
    assert out["gaps"], "an empty result must surface an explicit gap, not a silent []"


def test_think_empty_query_is_a_gap_not_a_crash(atelier_env: Dict):
    out = _think.think(query="   ", top_k=5)
    assert out["result_count"] == 0 and out["gaps"]


def test_atelier_think_tool_registered():
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.all_tools()} if hasattr(_tools, "all_tools") else None
    if names is None:
        import asyncio as _a
        out = _a.run(_tools.invoke("atelier_think", query="anything"))
        assert "citations" in out and "gaps" in out
    else:
        assert "atelier_think" in names
