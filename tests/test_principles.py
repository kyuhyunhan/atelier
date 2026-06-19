"""PR-24.5: principles/ tier — cross-project developer ethos."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest
import yaml

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import review as _rev


def _accept(observation: str, why: str, rule: str,
            project: str, topic: str = "general") -> str:
    cap = _cap.capture(
        observation=observation, why=why, rule=rule,
        working_dir=f"/Users/me/workspaces/{project}",
        session_id="s", hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem


def _read_fm(path: Path) -> Dict:
    from runtime.index.parse import split_frontmatter
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


# ── add ────────────────────────────────────────────────────────────────────


def test_add_writes_principle(atelier_env: Dict) -> None:
    # RFC 0005 §7.1 — a principle is BORN AS A v7 CLAIM at surfacing:always, not
    # a learnings/principles/ file. The directory tier collapsed to claim fields.
    out = _pr.add(
        title="prefer real db in integration tests",
        rule="integration tests must hit a real database, not mocks.",
        why="mocked tests pass while prod migration fails (lexio 2026-03; bht 2026-04).",
        evidence=["learnings/notes/2026-01/foo.md"],
        coverage="cross-project",
        priority="always-inject",
    )
    p = Path(out["path"])
    assert p.exists()
    # No legacy directory: the node lives under the atomic claims tree.
    assert "graph/atomic/claims/" in str(p)
    assert "learnings/principles/" not in str(p)
    fm = _read_fm(p)
    assert fm["schema_version"] == 7
    assert fm["kind"] == "claim"
    assert fm["domain"] == "operational"
    # priority maps onto the surfacing tier; ac_status is the accepted state.
    assert fm["surfacing"] == "always"
    assert fm["ac_status"] == "passed"
    # legacy facets are preserved as flat fields (bootstrap/MCP keep working).
    assert fm["coverage"] == "cross-project"
    assert fm["priority"] == "always-inject"
    assert fm["principle_tier"] is True
    body = p.read_text()
    assert "## Rule" in body and "mocks" in body
    assert "## Evidence" in body


def test_add_refuses_collision(atelier_env: Dict) -> None:
    _pr.add(title="rule one", rule="r", why="w",
            slug="rule-one")
    with pytest.raises(FileExistsError):
        _pr.add(title="another", rule="r", why="w", slug="rule-one")


def test_add_rejects_bad_priority(atelier_env: Dict) -> None:
    with pytest.raises(ValueError, match="priority"):
        _pr.add(title="t", rule="r", why="w", priority="urgent")


# ── synthesize ─────────────────────────────────────────────────────────────


def test_synthesize_from_two_accepted_learnings(atelier_env: Dict) -> None:
    s1 = _accept("lexio mock bug", "mocked db diverged from prod migration",
                  "use real db in IT", project="lexio", topic="db-tests")
    s2 = _accept("bht mock bug", "same issue on bht repo", "real db only",
                  project="bht", topic="db-tests")

    out = _pr.synthesize(
        source_slugs=[s1, s2],
        title="real db over mocks",
        rule="integration tests must use the real db.",
        why="mocked tests silently diverge from prod schema; cost 2 incidents.",
        coverage="cross-project",
        priority="always-inject",
    )
    p = Path(out["path"])
    fm = _read_fm(p)
    assert fm["coverage"] == "cross-project"
    assert fm["priority"] == "always-inject"
    # Two evidence backlinks resolved to vault-relative paths. RFC 0005 §7.1:
    # an accepted learning is now a v7 claim node, so evidence resolves under
    # the atomic claims tree (was the legacy learnings/notes/ store).
    assert len(fm["evidence"]) == 2
    assert all(e.startswith("graph/atomic/claims/") for e in fm["evidence"])
    body = p.read_text()
    for e in fm["evidence"]:
        assert f"[[{e}]]" in body


def test_synthesize_leaves_scaffold_when_rule_blank(atelier_env: Dict) -> None:
    s = _accept("x", "y", "rule x", project="lexio", topic="db-tests")
    out = _pr.synthesize(source_slugs=[s], title="todo principle")
    body = Path(out["path"]).read_text()
    assert "(fill in: the principle in one or two sentences)" in body
    assert out["fields_to_fill"] == ["rule", "why"]


def test_synthesize_refuses_missing_source(atelier_env: Dict) -> None:
    with pytest.raises(FileNotFoundError):
        _pr.synthesize(source_slugs=["does-not-exist"])


# ── list / archive ─────────────────────────────────────────────────────────


def test_list_filters_by_priority(atelier_env: Dict) -> None:
    _pr.add(title="always-1", rule="r", why="w", priority="always-inject")
    _pr.add(title="relevant-1", rule="r", why="w", priority="on-relevant-prompt")
    out = _pr.list_all(priority="always-inject")
    assert len(out) == 1
    assert out[0]["priority"] == "always-inject"


def test_archive_is_field_transition_not_a_move(atelier_env: Dict) -> None:
    # RFC 0005 §7.1 — archive is ac_status → retracted IN PLACE. The file does
    # NOT move to a legacy archived/ directory; entry_id is preserved.
    out = _pr.add(title="stale", rule="r", why="w", slug="stale-one")
    res = _pr.archive(slug="stale-one", reason="outdated")
    assert res["path"] == out["path"]            # same file, not moved
    assert Path(res["path"]).exists()
    assert "archived/" not in res["path"]
    fm = _read_fm(Path(res["path"]))
    assert fm["ac_status"] == "retracted"
    assert fm["archive_reason"] == "outdated"
    assert fm["entry_id"] == out["entry_id"]     # stable handle preserved
    # an archived principle drops out of the default listing.
    assert all(it["slug"] != "stale-one" for it in _pr.list_all())


# ── proposed → approve / reject (dream cycle ③, field transitions) ──────────


def test_synthesize_draft_then_approve(atelier_env: Dict) -> None:
    s = _accept("x", "y", "rule x", project="lexio", topic="db-tests")
    draft = _pr.synthesize(source_slugs=[s], title="draft principle",
                            rule="r", why="w", priority="always-inject")
    p = Path(draft["path"])
    fm = _read_fm(p)
    # a proposed draft is born pending, NOT injected (surfacing still query-ish).
    assert fm["ac_status"] == "pending"
    assert draft["slug"] in {it["slug"] for it in _pr.review_proposed()["items"]}
    # not listed as accepted before approval.
    assert draft["slug"] not in {it["slug"]
                                 for it in _pr.list_all(status="accepted")}

    res = _pr.approve(slug=draft["slug"])
    assert res["status"] == "accepted"
    fm2 = _read_fm(p)
    assert fm2["entry_id"] == fm["entry_id"]          # preserved, no move
    assert fm2["ac_status"] == "passed"
    assert fm2["surfacing"] == "always"              # priority=always-inject
    assert draft["slug"] in {it["slug"]
                             for it in _pr.list_all(status="accepted")}


def test_reject_proposed_is_retracted_field(atelier_env: Dict) -> None:
    s = _accept("x", "y", "rule x", project="lexio", topic="db-tests")
    draft = _pr.synthesize(source_slugs=[s], title="bad draft",
                            rule="r", why="w",
                            source_entry_ids=["a", "b", "c"])
    res = _pr.reject(slug=draft["slug"], reason="not a real pattern")
    assert res["status"] == "archived"
    fm = _read_fm(Path(res["path"]))
    assert fm["ac_status"] == "retracted"
    # a rejected cluster is never re-proposed (dedup consults retracted claims).
    cover = _pr.find_covering_principle(["a", "b", "c"])
    assert cover is not None and cover["status"] == "archived"


def test_no_legacy_principle_dirs_written(atelier_env: Dict) -> None:
    # The gate clause: no runtime write path targets the legacy directories.
    root = atelier_env["gorae"]
    _pr.add(title="p1", rule="r", why="w", priority="always-inject")
    s = _accept("x", "y", "rx", project="lexio")
    d = _pr.synthesize(source_slugs=[s], title="p2", rule="r", why="w")
    _pr.approve(slug=d["slug"])
    _pr.add(title="p3", rule="r", why="w", slug="p3")
    _pr.archive(slug="p3", reason="x")
    for legacy in ("learnings/principles", "learnings/archived",
                   "provenance/learning/principles",
                   "provenance/learning/archived"):
        assert not (root / legacy).exists(), f"legacy dir written: {legacy}"


# ── MCP dispatch ───────────────────────────────────────────────────────────


def test_mcp_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    expected = {
        "atelier_principle_add",
        "atelier_principle_synthesize",
        "atelier_principle_list",
        "atelier_principle_archive",
    }
    assert expected <= names


def test_mcp_dispatch_principle_add(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_principle_add",
            title="mcp-added",
            rule="r",
            why="w",
        )
    out = asyncio.run(go())
    assert Path(out["path"]).exists()
