"""PR-19: learning_capture handler + atelier-mcp-call CLI."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict

import pytest

from runtime.service import tools as _tools
from runtime.service.learnings import capture as _cap


def _read_fm(path: Path) -> dict:
    from runtime.index.parse import split_frontmatter
    fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def test_capture_is_born_as_a_query_pending_claim(atelier_env: Dict) -> None:
    """RFC 0005 §7.1: a capture is born DIRECTLY as a v7 Claim
    (domain:operational, surfacing:query, ac_status:pending, generated_by:<hook>)
    under the atomic claims tree — NOT a legacy candidates/ file."""
    result = _cap.capture(
        observation="search returns nothing for tilde queries",
        why="fts5 ignores tilde tokens; need fallback",
        rule="enable fallback for punctuation in queries",
        working_dir="/Users/me/workspaces/lexio",
        session_id="abc",
        hook="Stop",
    )
    path = Path(result["path"])
    assert path.exists()
    assert "graph/atomic/" in str(path)   # flat L2 graph (RFC 0005 P9.4)
    assert "graph/atomic/claims/" not in str(path)   # kind subdir is gone
    assert "learnings/candidates/" not in str(path)
    fm = _read_fm(path)
    assert fm["schema_version"] == 7
    assert fm["kind"] == "claim"
    assert fm["domain"] == "operational"
    assert fm["surfacing"] == "query"
    assert fm["ac_status"] == "pending"
    # generated_by is the PROV activity (schema enum ingest|atomize|promote|
    # dream) — a born-as-claim capture is `ingest`; the hook is in `hook`.
    assert fm["generated_by"] == "ingest"
    assert fm["hook"] == "Stop"
    assert fm["agent_kind"] == "claude-code"
    assert fm["attributed_to"] == "claude-code"
    assert fm["project_hint"] == "lexio"
    assert fm["project"] == "lexio"
    assert fm["entry_id"] == result["entry_id"]
    # born WITH a thin session Source it derives_from (PROV chain at birth).
    assert fm["derived_from"] == [result["source_entry_id"]]


def test_capture_carries_session_metadata_on_claim(atelier_env: Dict) -> None:
    """RFC 0005 P10: the session metadata (session_id / working_dir /
    agent_kind / hook / captured_at) lives ON the claim as §4.3 extension
    fields — NOT on a per-learning session-source stub."""
    result = _cap.capture(
        observation="x happens under y", why="because z",
        working_dir="/Users/me/workspaces/lexio", session_id="sess-1",
        hook="manual",
    )
    fm = _read_fm(Path(result["path"]))
    assert fm["session_id"] == "sess-1"
    assert fm["working_dir"] == "/Users/me/workspaces/lexio"
    assert fm["hook"] == "manual"
    assert fm["agent_kind"] == "claude-code"
    assert fm["captured_at"]


def test_capture_derives_from_single_shared_source(atelier_env: Dict) -> None:
    """RFC 0005 P10: every operational claim derives_from ONE shared
    operational-capture Source. Two captures share the same source id, and
    exactly ONE shared source node exists in the vault — no per-learning stubs."""
    a = _cap.capture(observation="alpha", why="because a",
                     working_dir="/w", session_id="s1", hook="manual")
    b = _cap.capture(observation="beta", why="because b",
                     working_dir="/w", session_id="s2", hook="Stop")
    assert a["source_entry_id"] == b["source_entry_id"]
    fm_a = _read_fm(Path(a["path"]))
    fm_b = _read_fm(Path(b["path"]))
    assert fm_a["derived_from"] == [a["source_entry_id"]]
    assert fm_b["derived_from"] == [b["source_entry_id"]]

    vault = atelier_env["gorae"]
    sources = [p for p in vault.rglob("*.md")
               if _read_fm(p).get("kind") == "source"
               and _read_fm(p).get("entry_id") == a["source_entry_id"]]
    assert len(sources) == 1
    sfm = _read_fm(sources[0])
    assert sfm["domain"] == "inbox"
    assert sfm["sensitivity"] == "public"
    # No per-learning session stub is created (RFC 0005 P10).
    stubs = [p for p in (vault / "raw" / "inbox").rglob("*.md")
             if p.name.startswith("session-") or p.name.startswith("learning-")]
    assert stubs == []


def test_capture_source_lands_in_raw_not_graph(atelier_env: Dict) -> None:
    """RFC 0005 §3 round-trip: the shared operational Source is an L1 node in the
    content tree (raw/inbox), NEVER under graph/. The claim stays flat in graph/."""
    result = _cap.capture(
        observation="capture writes its source to raw, not graph",
        why="a Source is an L1 node in the content tree",
        working_dir="/Users/me/workspaces/lexio", session_id="sess-raw",
        hook="manual",
    )
    vault = atelier_env["gorae"]
    matches = [p for p in vault.rglob("*.md")
               if _read_fm(p).get("entry_id") == result["source_entry_id"]]
    assert len(matches) == 1
    src_path = matches[0]
    rel = src_path.relative_to(vault).as_posix()
    # the shared source lives under the inbox intake (raw/inbox) …
    assert rel.startswith("raw/inbox/"), rel
    assert rel.endswith("operational-capture.md"), rel
    # … and decidedly NOT in the graph tree.
    assert "graph/" not in rel
    # the claim it derives_from still resolves the source by id (stable handle).
    claim_fm = _read_fm(Path(result["path"]))
    assert claim_fm["derived_from"] == [result["source_entry_id"]]
    assert "graph/atomic/" in result["path"]   # L2 claim stays flat in graph/
    assert "graph/atomic/claims/" not in result["path"]   # kind subdir gone (P9.4)


def test_capture_resolves_project_to_is_about_entity(atelier_env: Dict) -> None:
    """project_hint/touches resolve-or-create into is_about Entity ids so the
    claim is wired into the graph at birth (RFC 0005 §7.1)."""
    result = _cap.capture(
        observation="overlay bug", why="needs a stable key",
        rule="stabilize keys", working_dir="/Users/me/workspaces/lexio",
        touches=["react-rendering"], hook="manual",
    )
    fm = _read_fm(Path(result["path"]))
    assert len(fm["is_about"]) == 2          # project + one touched concept
    # Entity nodes live FLAT under graph/atomic/ (P9.4), keyed by the `kind`
    # field — so filter on kind, not on a kind subdir.
    from runtime.structure import resolver as _structure
    ents = atelier_env["gorae"] / _structure.atomic_entity_dir()
    ent_ids = {
        _read_fm(p).get("entry_id")
        for p in ents.rglob("*.md")
        if _read_fm(p).get("kind") == "entity"
    }
    assert set(fm["is_about"]) <= ent_ids    # every is_about points at a real node


def test_capture_inside_vault_tags_atelier_self(atelier_env: Dict) -> None:
    """working_dir under the vault root → project_hint = atelier-self."""
    cwd = atelier_env["gorae"] / "wiki"
    cwd.mkdir(exist_ok=True)
    result = _cap.capture(
        observation="dogfooding atelier itself",
        why="confirms the engine works on its own vault",
        working_dir=str(cwd),
        hook="manual",
    )
    fm = _read_fm(Path(result["path"]))
    assert fm["project_hint"] == "atelier-self"


# ── substance gate (C) ──────────────────────────────────────────────────────


def test_capture_flags_empty_why_but_writes(atelier_env: Dict) -> None:
    """RFC 0004 phase 2: an observation with no why is NO LONGER rejected.
    It is written, flagged why_status=missing, and the result carries a soft
    why_missing nudge (require_why defaults True)."""
    result = _cap.capture(observation="something happened", hook="Stop")
    assert "skipped" not in result
    path = Path(result["path"])
    assert path.exists()
    assert result["why_status"] == "missing"
    assert result["why_missing"] is True
    assert _read_fm(path)["why_status"] == "missing"


def test_capture_require_why_false_suppresses_nudge(atelier_env: Dict) -> None:
    """With require_why=False (session-end hook / absorbed memory), an empty
    why still writes + flags missing, but emits no why_missing nudge."""
    result = _cap.capture(observation="hook-derived observation",
                          require_why=False, hook="SessionEnd")
    assert result["why_status"] == "missing"
    assert "why_missing" not in result
    assert Path(result["path"]).exists()


def test_capture_present_why_sets_status(atelier_env: Dict) -> None:
    result = _cap.capture(observation="x happens under y",
                          why="because z, which prevents w", hook="manual")
    assert result["why_status"] == "present"
    assert "why_missing" not in result
    assert _read_fm(Path(result["path"]))["why_status"] == "present"


def test_capture_rejects_stub_observation(atelier_env: Dict) -> None:
    """A bare hook stub (no real observation, no why) → no-substance."""
    result = _cap.capture(
        observation="(hook=Stop) session_id=abc-123",
        hook="Stop",
    )
    assert result["skipped"] is True
    assert result["reason"] == "no-substance"


def test_capture_accepts_when_why_present(atelier_env: Dict) -> None:
    result = _cap.capture(
        observation="search returns nothing for tilde queries",
        why="fts5 ignores tilde tokens; users see a silent empty result",
        hook="manual",
    )
    assert Path(result["path"]).exists()


def test_capture_require_why_false_writes(atelier_env: Dict) -> None:
    """Sources with free-form rationale (e.g. absorbed Claude memory) write
    without a why and without a nudge."""
    result = _cap.capture(
        observation="absorbed memory with prose rationale inline",
        require_why=False, hook="manual",
    )
    assert Path(result["path"]).exists()
    assert "why_missing" not in result


def test_capture_distinct_lessons_get_distinct_claims(atelier_env: Dict) -> None:
    """Two captures with DIFFERENT statements land as two distinct claims."""
    a = _cap.capture(observation="alpha", why="because a", rule="rule alpha",
                     hook="Stop")
    b = _cap.capture(observation="beta", why="because b", rule="rule beta",
                     hook="Stop")
    assert a["path"] != b["path"]
    assert a["entry_id"] != b["entry_id"]


def test_capture_identical_lesson_is_idempotent(atelier_env: Dict) -> None:
    """RFC 0005 §5: the claim id is content-addressed (norm(statement) |
    derived_from). Re-capturing the IDENTICAL lesson from the same session
    converges on the same claim (the dedup key), not a duplicate file."""
    a = _cap.capture(observation="alpha", why="because a", rule="same rule",
                     session_id="s", working_dir="/w", hook="Stop")
    b = _cap.capture(observation="alpha", why="because a", rule="same rule",
                     session_id="s", working_dir="/w", hook="Stop")
    assert a["entry_id"] == b["entry_id"]
    assert a["path"] == b["path"]


def test_mcp_tool_dispatch_invokes_capture(atelier_env: Dict) -> None:
    """The MCP tool registration must wire through tools.invoke()."""
    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_learning_capture",
            observation="invoked via MCP",
            why="verifies the tool wiring end to end",
            hook="manual",
        )
    out = asyncio.run(go())
    assert Path(out["path"]).exists()


def test_capture_survives_logging_failure(atelier_env: Dict,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Observability must not break the non-blocking capture contract: if the
    handler's log call raises (e.g. read-only fs), the already-written
    candidate's result is still returned."""
    def boom(*a, **k):  # noqa: ANN001
        raise OSError("log sink unavailable")
    monkeypatch.setattr(_tools._log, "info", boom)
    monkeypatch.setattr(_tools._log, "warn", boom)

    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_learning_capture",
            observation="logging is down but the lesson is real",
            why="captures must never be lost to a log sink error",
            hook="manual",
        )
    out = asyncio.run(go())
    assert Path(out["path"]).exists()


def test_mcp_tool_dispatch_flags_empty_why(atelier_env: Dict) -> None:
    """The tool layer writes an empty-why capture and flags it missing."""
    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_learning_capture",
            observation="hook fired but no judgement",
            hook="Stop",
        )
    out = asyncio.run(go())
    assert "skipped" not in out
    assert out["why_status"] == "missing"
    assert Path(out["path"]).exists()


def test_capture_refuses_when_vault_missing(atelier_env: Dict,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Vault root must exist; otherwise we surface a real error. (Provide
    a why so the substance gate passes and we reach the vault check.)"""
    import shutil as _sh
    _sh.rmtree(atelier_env["gorae"])
    with pytest.raises(FileNotFoundError):
        _cap.capture(observation="foo", why="bar", hook="Stop")
