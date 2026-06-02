"""Project resolution SSOT — the accessor every path shares (learning 1446).

The keystone here is `test_resolution_converges_*`: it exercises the three
real call paths (capture's tag, bootstrap's injected project, recall's boost
key) and asserts they agree for the same input. If a future change
reintroduces a divergent per-path derivation, this test fails — the silent
write-key/read-key mismatch becomes impossible to ship unnoticed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest
import yaml

from runtime.service.learnings import bootstrap as _bs
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import project as _proj


# ── helpers ───────────────────────────────────────────────────────────────


def _set_project_map(home: Path, mapping: Dict[str, str]) -> None:
    cfg_path = home / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data.setdefault("learnings", {})["project_map"] = mapping
    cfg_path.write_text(yaml.safe_dump(data))


def _vault(atelier_env: Dict) -> Path:
    return atelier_env["gorae"]            # librarian-territory == vault root


# ── precedence chain ────────────────────────────────────────────────────────


def test_explicit_hint_wins(atelier_env: Dict) -> None:
    res = _proj.resolve_project("/Users/me/workspaces/lexio", explicit="forced")
    assert res.slug == "forced"
    assert res.source == "explicit"


def test_basename_is_the_fallback(atelier_env: Dict) -> None:
    res = _proj.resolve_project("/Users/me/workspaces/lexio")
    assert res.slug == "lexio"
    assert res.source == "basename"


def test_config_map_exact_match_beats_basename(atelier_env: Dict) -> None:
    _set_project_map(atelier_env["home"], {"/work/foo": "mapped-foo"})
    res = _proj.resolve_project("/work/foo")
    assert res.slug == "mapped-foo"
    assert res.source == "config-map"


def test_config_map_prefix_match(atelier_env: Dict) -> None:
    _set_project_map(atelier_env["home"], {"/work/repo": "repo-proj"})
    res = _proj.resolve_project("/work/repo/services/api")
    assert res.slug == "repo-proj"
    assert res.source == "config-map"


def test_config_map_longest_prefix_wins(atelier_env: Dict) -> None:
    _set_project_map(atelier_env["home"],
                     {"/work": "outer", "/work/repo": "inner"})
    res = _proj.resolve_project("/work/repo/sub")
    assert res.slug == "inner"


def test_marker_file_beats_basename(atelier_env: Dict, tmp_path: Path) -> None:
    proj_dir = tmp_path / "weird-folder-name"
    proj_dir.mkdir()
    (proj_dir / ".atelier-project").write_text("canonical-name\n")
    res = _proj.resolve_project(str(proj_dir))
    assert res.slug == "canonical-name"
    assert res.source == "marker"


def test_marker_walks_up_to_a_parent(atelier_env: Dict, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    nested = root / "src" / "deep"
    nested.mkdir(parents=True)
    (root / ".atelier-project").write_text("from-root\n")
    res = _proj.resolve_project(str(nested))
    assert res.slug == "from-root"


def test_vault_self_for_dirs_inside_the_vault(atelier_env: Dict) -> None:
    inside = _vault(atelier_env) / "wiki" / "entities"
    res = _proj.resolve_project(str(inside))
    assert res.slug == _proj.SELF_SLUG
    assert res.source == "vault-self"


def test_no_working_dir_yields_none(atelier_env: Dict) -> None:
    res = _proj.resolve_project(None)
    assert res.slug is None
    assert res.source == "none"
    assert res.known is False


# ── known / unknown ─────────────────────────────────────────────────────────


def test_unknown_when_no_by_project_dir(atelier_env: Dict) -> None:
    res = _proj.resolve_project("/Users/me/workspaces/lexio")
    assert res.slug == "lexio"
    assert res.known is False


def test_known_when_by_project_dir_exists(atelier_env: Dict) -> None:
    (_vault(atelier_env) / "learnings" / "accepted" / "by-project" / "lexio") \
        .mkdir(parents=True)
    res = _proj.resolve_project("/Users/me/workspaces/lexio")
    assert res.slug == "lexio"
    assert res.known is True


# ── project identity is local: a git remote is never consulted ──────────────


def test_git_remote_is_ignored_basename_wins(atelier_env: Dict,
                                             tmp_path: Path) -> None:
    """Even inside a real git repo whose remote basename differs from the
    folder, resolution stays local: the folder basename wins, the remote is
    never read. (Guards against reintroducing a remote-coupled layer that
    would silently re-key monorepo subdirs.)"""
    import shutil, subprocess
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = tmp_path / "frontend"            # folder name != remote name
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://github.com/acme/app-frontends.git"], check=True)
    res = _proj.resolve_project(str(repo))
    assert res.slug == "frontend"
    assert res.source == "basename"


# ── keystone: convergence across the three call paths ───────────────────────


def test_resolution_converges_across_capture_bootstrap_recall(
        atelier_env: Dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """capture (write key), bootstrap (§B key), and recall (boost key) must
    all derive the SAME project slug for the same working_dir. This is the
    regression guard for learning 1446 — divergence here is the silent
    write/read mismatch we are eliminating."""
    D = "/Users/me/workspaces/lexio"

    cap = _cap.capture(observation="obs", why="why", working_dir=D)
    boot = _bs.bootstrap(working_dir=D)

    # recall: intercept the project the handler resolves before it hits FTS.
    recorded: Dict[str, object] = {}
    from runtime.service.learnings import recall as _rc

    def fake_recall(*, query, project, **kw):
        recorded["project"] = project
        return {"items": [], "count": 0}

    monkeypatch.setattr(_rc, "recall", fake_recall)

    from runtime.service import auth, tools as _tools
    sess = auth.Session(transport="mcp-http", working_dir=D,
                        caller="test", claims=frozenset())
    tok = _tools.set_session(sess)
    try:
        asyncio.run(_tools.invoke("atelier_recall", query="q"))
    finally:
        _tools._current.reset(tok)

    assert cap["project_hint"] == boot["project"] == recorded["project"] == "lexio"
