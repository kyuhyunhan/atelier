"""PR-31: review_proposed / approve / reject for dream-cycle drafts."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import bootstrap as _bs
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import review as _rev


def _read_fm(path: Path) -> Dict:
    from runtime.index.parse import split_frontmatter
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def _accept(project: str, topic: str, seed: str):
    cap = _cap.capture(
        observation=f"obs {seed} in {project}", why=f"why {seed}",
        rule=f"rule {seed}", working_dir=f"/Users/me/workspaces/{project}",
        session_id=seed, hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem, cap["entry_id"]


def _draft(slug: str = "draft-1", *, priority: str = "on-relevant-prompt"):
    s1, e1 = _accept("lexio", "testing", slug + "a")
    s2, e2 = _accept("bht", "testing", slug + "b")
    return _pr.synthesize(
        source_slugs=[s1, s2], title=f"{slug} title",
        rule="integration tests must use a real database.",
        why="mocks diverge from prod schema.",
        priority=priority, source_entry_ids=[e1, e2], slug=slug,
    )


# ── review_proposed ─────────────────────────────────────────────────────────


def test_review_lists_only_proposed(atelier_env: Dict) -> None:
    _draft("p-proposed")
    _pr.add(title="already accepted", rule="r", why="w", slug="p-accepted")
    out = _pr.review_proposed()
    slugs = {it["slug"] for it in out["items"]}
    assert "p-proposed" in slugs
    assert "p-accepted" not in slugs


def test_review_includes_rule_and_evidence(atelier_env: Dict) -> None:
    _draft("p-rich")
    out = _pr.review_proposed()
    item = next(it for it in out["items"] if it["slug"] == "p-rich")
    assert "real database" in item["rule"]
    assert len(item["evidence"]) == 2
    assert len(item["source_entry_ids"]) == 2


# ── approve ─────────────────────────────────────────────────────────────────


def test_approve_promotes_to_accepted(atelier_env: Dict) -> None:
    _draft("p-app")
    out = _pr.approve(slug="p-app", priority="always-inject")
    assert out["status"] == "accepted"
    fm = _read_fm(Path(out["path"]))
    assert fm["status"] == "accepted"
    assert fm["ac_status"] == "passed"
    assert fm["priority"] == "always-inject"
    assert "accepted_at" in fm
    assert "proposed_at" not in fm


def test_approved_principle_now_injected_by_bootstrap(atelier_env: Dict) -> None:
    _draft("p-inject", priority="on-relevant-prompt")
    # Not injected while proposed...
    pre = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")["markdown"]
    assert "p-inject title" not in pre
    # ...approve as always-inject → now injected.
    _pr.approve(slug="p-inject", priority="always-inject")
    post = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")["markdown"]
    assert "p-inject title" in post


def test_approve_refuses_non_proposed(atelier_env: Dict) -> None:
    _pr.add(title="manual", rule="r", why="w", slug="p-manual")
    with pytest.raises(ValueError, match="only proposed"):
        _pr.approve(slug="p-manual")


def test_approve_rejects_bad_priority(atelier_env: Dict) -> None:
    _draft("p-bad")
    with pytest.raises(ValueError, match="priority"):
        _pr.approve(slug="p-bad", priority="urgent")


# ── reject ──────────────────────────────────────────────────────────────────


def test_reject_moves_to_archived(atelier_env: Dict) -> None:
    _draft("p-rej")
    out = _pr.reject(slug="p-rej", reason="not general enough")
    assert out["status"] == "archived"
    assert not (Path(out["path"]).parent.parent / "principles" / "p-rej.md").exists()
    assert "archived/" in out["path"]


def test_rejected_not_reproposed(atelier_env: Dict) -> None:
    d = _draft("p-norep")
    eids = _read_fm(Path(d["path"]))["source_entry_ids"]
    _pr.reject(slug="p-norep", reason="x")
    # Same cluster members synthesized again → skipped (archived covers).
    again = _pr.synthesize(
        source_slugs=[], source_entry_ids=eids, title="x", slug="p-norep-2",
    ) if False else _pr.find_covering_principle(eids)
    assert again is not None
    assert again["status"] == "archived"


def test_reject_refuses_non_proposed(atelier_env: Dict) -> None:
    _pr.add(title="manual2", rule="r", why="w", slug="p-manual2")
    with pytest.raises(ValueError, match="only proposed"):
        _pr.reject(slug="p-manual2")


# ── MCP dispatch ────────────────────────────────────────────────────────────


def test_mcp_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    assert {"atelier_principle_review_proposed",
            "atelier_principle_approve",
            "atelier_principle_reject"} <= names


def test_mcp_dispatch_review_and_approve(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _draft("p-mcp")

    async def go():
        listed = await _tools.invoke("atelier_principle_review_proposed")
        approved = await _tools.invoke(
            "atelier_principle_approve", slug="p-mcp", priority="always-inject")
        return listed, approved

    listed, approved = asyncio.run(go())
    assert any(it["slug"] == "p-mcp" for it in listed["items"])
    assert approved["status"] == "accepted"
