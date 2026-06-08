"""PR-25/c: session-start context injection."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import bootstrap as _bs
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import review as _rev


def _accept(project: str, topic: str = "general") -> str:
    cap = _cap.capture(
        observation=f"obs for {project}",
        why=f"why for {project}",
        rule=f"rule for {project}",
        working_dir=f"/Users/me/workspaces/{project}",
        session_id="s", hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem


# ── basic empty-vault behaviour ───────────────────────────────────────────


def test_bootstrap_empty_vault_returns_friendly_placeholder(atelier_env: Dict) -> None:
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    assert "atelier" in out["markdown"]
    assert "no principles or per-project learnings yet" in out["markdown"]
    assert out["project"] == "lexio"
    assert out["principles_count"] == 0


# ── principles section ────────────────────────────────────────────────────


def test_bootstrap_injects_always_inject_principles(atelier_env: Dict) -> None:
    _pr.add(title="prefer real db",
             rule="integration tests must hit a real db.",
             why="mocks diverge.",
             priority="always-inject")
    _pr.add(title="manual-only one",
             rule="x", why="y", priority="manual-only")

    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    md = out["markdown"]
    assert "principles (always-inject)" in md
    assert "prefer real db" in md
    # Manual-only priority is NOT injected at session start.
    assert "manual-only one" not in md


# ── per-project section ───────────────────────────────────────────────────


def test_bootstrap_includes_project_learnings(atelier_env: Dict) -> None:
    _accept("lexio", topic="db-tests")
    _accept("lexio", topic="rendering")
    _accept("bht", topic="db-tests")

    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    md = out["markdown"]
    assert "learnings for project `lexio`" in md
    # bht is a separate project; must not appear in lexio's bootstrap.
    assert "bht" not in md.lower() or md.lower().count("bht") == 0


def test_bootstrap_respects_max_chars(atelier_env: Dict) -> None:
    for i in range(20):
        _pr.add(title=f"principle {i}",
                 rule="x" * 200, why="y" * 200,
                 priority="always-inject",
                 slug=f"p-{i}")
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                         max_chars=500)
    assert out["char_count"] <= 500 + 32   # allow newline budget
    assert "_(truncated)_" in out["markdown"]


# ── MCP dispatch ──────────────────────────────────────────────────────────


def test_mcp_session_bootstrap_dispatch(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _pr.add(title="rule one", rule="r", why="w", priority="always-inject")

    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_session_bootstrap",
            working_dir="/Users/me/workspaces/lexio",
        )
    out = asyncio.run(go())
    assert out["principles_count"] == 1
    assert "rule one" in out["markdown"]


def test_bootstrap_project_inferred_from_session_when_arg_missing(
        atelier_env: Dict) -> None:
    """When the caller omits working_dir, the MCP wrapper should fall
    back to Session.working_dir."""
    from runtime.service import auth, tools as _tools
    sess = auth.Session(
        agent_kind="claude-code",
        transport="mcp-http",
        working_dir="/Users/me/workspaces/lexio",
        caller="test",
        claims=frozenset(),
    )
    tok = _tools.set_session(sess)
    try:
        async def go() -> Dict:
            return await _tools.invoke("atelier_session_bootstrap")
        out = asyncio.run(go())
    finally:
        _tools._current.reset(tok)
    assert out["project"] == "lexio"


# ── project resolution provenance + loud-on-unknown banner ─────────────────


def test_bootstrap_surfaces_project_provenance(atelier_env: Dict) -> None:
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    assert out["project_source"] == "basename"
    assert out["project_known"] is False


def test_bootstrap_warns_loudly_on_unknown_project(atelier_env: Dict) -> None:
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    # No by-project/lexio dir → the banner must lead the block.
    assert "project_map" in out["markdown"]
    assert out["markdown"].lstrip().startswith("ℹ️")


def test_bootstrap_no_banner_when_project_known(atelier_env: Dict) -> None:
    _accept("lexio", topic="db-tests")      # creates by-project/lexio
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    assert out["project_known"] is True
    assert "project_map" not in out["markdown"]


def test_unknown_banner_coexists_with_empty_vault_placeholder(
        atelier_env: Dict) -> None:
    """The banner must not suppress the friendly empty-vault placeholder:
    both should appear for a brand-new project in an empty vault."""
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    assert "project_map" in out["markdown"]                 # banner
    assert "no principles or per-project learnings yet" in out["markdown"]


# ── Phase 2: cross-cutting via explicit `touches` (folder-free §B) ──────────


def test_bootstrap_cross_cuts_on_explicit_touches(atelier_env: Dict) -> None:
    """A learning captured in project `lexio` that explicitly `touches` a
    concept should surface in project `app`'s bootstrap when `app` also has a
    learning touching that concept — connection by idea, not folder."""
    import yaml as _yaml
    from runtime.index.parse import split_frontmatter
    from runtime.service.learnings import store as _store

    a = _accept("app", topic="layering")
    lx = _accept("lexio", topic="architecture")
    # `touches` isn't set by accept; tag both notes with a shared concept. The
    # flat store (RFC 0001) means we locate each by stem, not a by-topic path.
    vault = atelier_env["gorae"]            # vault root
    by_stem = {p.stem: p for p in _store.iter_accepted_files(vault)}
    for stem in (a, lx):
        p = by_stem[stem]
        fm, body = split_frontmatter(p.read_text())
        fm["touches"] = ["dependency-direction"]
        p.write_text("---\n" + _yaml.safe_dump(fm, sort_keys=False) + "---\n" + body)

    out = _bs.bootstrap(working_dir="/Users/me/workspaces/app")
    md = out["markdown"]
    assert "related by concept" in md
    assert "lexio" in md or "architecture" in md   # the cross-project learning shows


def test_bootstrap_no_cross_cut_without_touches(atelier_env: Dict) -> None:
    """Sharing only a coarse `target_topic` must NOT cross-pollinate at session
    start (that would re-create folder-bucket noise). Isolation holds."""
    _accept("app", topic="db-tests")
    _accept("lexio", topic="db-tests")          # same topic, no `touches`
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/app")
    assert "related by concept" not in out["markdown"]
    assert "lexio" not in out["markdown"].lower()
