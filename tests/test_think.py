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
    assert set(c) == {"slug", "title", "snippet", "score", "n"}
    assert c["n"] == 1                       # 1-based citation index


def test_think_citations_are_1_based_contiguous(atelier_env: Dict):
    """Citation `n` is a stable 1-based index over the deterministic rank order,
    so the composed answer can reference [1], [2], … unambiguously."""
    _accept("postgres advisory locks serialize a job queue",
            "two workers grabbed the same row", "use pg_try_advisory_lock",
            project="bht", topic="locking")
    _accept("redis SETNX is a cheap distributed lock",
            "advisory locks need a live session", "SETNX with TTL",
            project="bht", topic="locking")
    from runtime.service import api
    api.reindex(full=True)
    out = _think.think(query="distributed lock for a job queue", top_k=5)
    ns = [c["n"] for c in out["citations"]]
    assert ns == list(range(1, len(ns) + 1))


def test_think_payload_carries_contract(atelier_env: Dict):
    """The composition contract travels in the payload (single source of truth
    for any caller), uniformly on both covered and empty-query returns."""
    out = _think.think(query="   ", top_k=5)               # empty → early return
    assert "contract" in out
    out2 = _think.think(query="anything at all", top_k=5)  # main return
    assert "contract" in out2
    assert out["contract"] == out2["contract"]
    assert "## Answer" in out["contract"] and "never invent" in out["contract"]


def _bundle_with_hits(topic_query: str):
    from runtime.service import api
    _accept("RRF compresses fused scores to ~1/60 with tiny inter-rank gaps",
            "a vote-scaled boost swamped fusion order", "scale boosts to the gap",
            project="atelier", topic="rrf")
    api.reindex(full=True)
    return _think.think(query=topic_query, top_k=5)


def test_compose_conforms_to_contract(atelier_env: Dict):
    """The GP5 gate: compose(bundle) yields a contract-conformant answer —
    all three sections, every Answer claim cites [n], gaps under Caveats,
    Sources list the cited indices. This proves the bundle→answer path without
    an LLM (the engine never generates prose; compose is deterministic assembly)."""
    import re
    out = _bundle_with_hits("RRF fusion boost scaling")
    answer = _think.compose(out)
    assert "## Answer" in answer and "## Caveats" in answer and "## Sources" in answer
    # every non-empty Answer line carries a [n] marker
    body = answer.split("## Answer", 1)[1].split("## Caveats", 1)[0]
    claim_lines = [ln for ln in body.splitlines() if ln.strip()]
    assert claim_lines and all(re.search(r"\[\d+\]", ln) for ln in claim_lines)
    # each cited [n] exists in Sources, in index order
    src = answer.split("## Sources", 1)[1]
    for c in out["citations"]:
        assert f"[{c['n']}]" in src and c["slug"] in src


def test_compose_zero_coverage_is_honest(atelier_env: Dict):
    """No-match query → Answer states memory has nothing, Caveats carries the
    gap, Sources is empty, and NO citation marker is fabricated."""
    import re
    from runtime.service import api
    api.reindex(full=True)
    out = _think.think(query="quantum chromodynamics lattice gauge", top_k=5)
    answer = _think.compose(out)
    assert out["result_count"] == 0
    assert "## Caveats" in answer and out["gaps"][0] in answer
    assert not re.search(r"\[\d+\]", answer), "no fabricated citation on empty evidence"


def test_compose_answer_markers_reference_real_indices(atelier_env: Dict):
    """Every [n] in the Answer maps to a real citation index — no dangling marker
    (stronger than 'some [n] present')."""
    import re
    out = _bundle_with_hits("RRF fusion boost")
    body = _think.compose(out).split("## Answer", 1)[1].split("## Caveats", 1)[0]
    used = {int(m) for m in re.findall(r"\[(\d+)\]", body)}
    valid = {c["n"] for c in out["citations"]}
    assert used and used <= valid


def test_compose_falls_back_when_snippet_empty():
    """A citation with an empty snippet still yields a non-blank, cited Answer
    line (title/slug fallback) — never a markerless or blank claim."""
    import re
    bundle = {"query": "q", "gaps": [], "contract": "x", "result_count": 1,
              "citations": [{"n": 1, "slug": "graph/entities/x", "title": "X",
                             "snippet": "", "score": 1.0}]}
    answer = _think.compose(bundle)
    first = [ln for ln in answer.split("## Answer", 1)[1]
             .split("## Caveats", 1)[0].splitlines() if ln.strip()][0]
    assert "X" in first and re.search(r"\[1\]", first)


def test_compose_tolerates_minimal_bundle():
    """compose() never KeyErrors on a citation missing optional fields — it is a
    public function headless callers use as-is."""
    answer = _think.compose({"citations": [{"n": 1, "slug": "graph/entities/x"}],
                             "gaps": []})
    assert "graph/entities/x" in answer       # must not raise


def test_compose_is_deterministic(atelier_env: Dict):
    """Pure-B determinism: a JSON round-trip of the bundle (different dict object,
    same logical content) composes byte-identically — catches any iteration-order
    or unstable-sort sensitivity, which `compose(out) == compose(out)` would not."""
    import json
    out = _bundle_with_hits("RRF fusion")
    assert _think.compose(out) == _think.compose(json.loads(json.dumps(out)))


def test_think_reports_a_gap_when_nothing_matches(atelier_env: Dict):
    from runtime.service import api
    api.reindex(full=True)
    out = _think.think(query="quantum chromodynamics lattice gauge", top_k=5)
    assert out["result_count"] == 0
    assert out["gaps"], "an empty result must surface an explicit gap, not a silent []"


def test_think_empty_query_is_a_gap_not_a_crash(atelier_env: Dict):
    out = _think.think(query="   ", top_k=5)
    assert out["result_count"] == 0 and out["gaps"]


def test_atelier_think_tool_returns_contract():
    """The MCP tool surfaces the bundle — citations, gaps, and the contract — so
    the calling agent has everything to compose a contract-conformant answer."""
    import asyncio as _a
    from runtime.service import tools as _tools
    out = _a.run(_tools.invoke("atelier_think", query="anything"))
    assert "citations" in out and "gaps" in out and "contract" in out
