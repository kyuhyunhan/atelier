"""PR-24: absorb Claude Code per-project memory into atelier learnings."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import absorb_claude as _ac


def _seed_claude(root: Path, project_dir: str, name: str, *,
                 type_: str, description: str = "",
                 body: str | None = None) -> Path:
    p = root / project_dir / "memory" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\ntype: {type_}\n---\n"
    # default body interpolates the name so each fixture is unique
    # (dedup is by body hash).
    real_body = body if body is not None else (
        f"## Rule\nrule for {name}: be explicit.\n"
    )
    p.write_text(fm + real_body, encoding="utf-8")
    return p


def _seed_index(root: Path, project_dir: str) -> None:
    """Create a MEMORY.md sibling — must be skipped by the walker."""
    p = root / project_dir / "memory" / "MEMORY.md"
    p.write_text("# index\n- [x](x.md)\n", encoding="utf-8")


# ── path decoding ─────────────────────────────────────────────────────────


def test_decode_cwd_dirname_round_trip() -> None:
    assert _ac.decode_cwd_dirname(
        "-Users-user-workspaces-project"
    ) == "/Users/user/workspaces/project"


def test_derive_project_takes_basename(atelier_env: Dict) -> None:
    _ac.derive_project.cache_clear()
    assert _ac.derive_project("-Users-user-workspaces-project") == "project"


# ── the encoding is LOSSY: a dir name may itself contain `-` ─────────────────
#
# These fixtures deliberately use HYPHENATED directory names. The original
# fixtures did not (`lexio`, later the generic `project`), and that is exactly
# why the defect survived: every real project whose folder contains a hyphen
# (`identity-hub`, `app-frontend`, `fe-shared`) decoded wrong, while the tests
# stayed green on the one shape that happens to be unambiguous.


def _encode(p: Path) -> str:
    """Encode a real path the way Claude Code does (`/` → `-`)."""
    return str(p).replace("/", "-")


def test_decode_disambiguates_hyphenated_dir_against_filesystem(
        tmp_path: Path) -> None:
    real = tmp_path / "org" / "identity-hub"
    real.mkdir(parents=True)
    # The encoded form is ambiguous: org/identity-hub vs org/identity/hub.
    assert _ac.decode_cwd_dirname(_encode(real)) == str(real)


def test_decode_prefers_longest_component_when_both_readings_exist(
        tmp_path: Path) -> None:
    """`org/app-frontend` and `org/app/frontend` encode to the SAME dirname —
    Claude Code itself commingles them, so no decoder can tell them apart. The
    tie-break (longest component first) is therefore arbitrary but must be
    STABLE: an unstable choice would re-key a project between absorb runs."""
    (tmp_path / "org" / "app-frontend").mkdir(parents=True)
    (tmp_path / "org" / "app" / "frontend").mkdir(parents=True)
    name = _encode(tmp_path / "org" / "app-frontend")
    assert _ac.decode_cwd_dirname(name) == str(tmp_path / "org" / "app-frontend")
    assert _ac.decode_cwd_dirname(name) == _ac.decode_cwd_dirname(name)


def test_unverified_decode_does_not_borrow_a_live_project_slug(
        atelier_env: Dict, tmp_path: Path) -> None:
    """A GONE project must not inherit a live project's identity. The config
    map matches by path PREFIX, so the guessed path for a deleted `app-fe`
    (`…/app/fe`) would otherwise map onto the real `…/app` project and pollute
    its recall boost. An orphan key is safer than a wrong real one."""
    import yaml
    live = tmp_path / "org" / "app"
    live.mkdir(parents=True)
    cfg_path = atelier_env["home"] / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data.setdefault("learnings", {})["project_map"] = {str(live): "org-app"}
    cfg_path.write_text(yaml.safe_dump(data))

    # `org/app-fe` does NOT exist → unverified decode → naive `org/app/fe`
    gone = _encode(tmp_path / "org" / "app-fe")
    _ac.derive_project.cache_clear()
    assert _ac.derive_project(gone) == "fe"       # orphan, NOT "org-app"


def test_decode_falls_back_when_path_is_gone() -> None:
    """A project absorbed on another machine (or since deleted) has no
    filesystem to probe — the naive all-separators decoding still applies."""
    assert _ac.decode_cwd_dirname(
        "-nonexistent-a-b-c") == "/nonexistent/a/b/c"


def test_derive_project_agrees_with_the_session_resolver(
        atelier_env: Dict, tmp_path: Path) -> None:
    """The whole point of routing through `project.resolve_project`: an
    absorbed claim must be keyed the SAME as the live session that produced it,
    or recall's project boost can never match (project.py's documented failure
    mode)."""
    from runtime.service.learnings import project as _project
    real = tmp_path / "org" / "identity-hub"
    real.mkdir(parents=True)
    _ac.derive_project.cache_clear()
    absorbed_slug = _ac.derive_project(_encode(real))
    session_slug = _project.resolve_project(str(real)).slug
    assert absorbed_slug == session_slug
    assert absorbed_slug == "identity-hub"      # not the mangled "hub"


def test_derive_project_is_memoized_per_encoded_dir(tmp_path: Path) -> None:
    """A batch absorbs many memories per project dir; resolution must not be
    repeated per file (it was ~5s/call before the `need_known` split)."""
    real = tmp_path / "org" / "identity-hub"
    real.mkdir(parents=True)
    name = _encode(real)
    _ac.derive_project.cache_clear()
    _ac.derive_project(name)
    before = _ac.derive_project.cache_info()
    _ac.derive_project(name)
    assert _ac.derive_project.cache_info().hits == before.hits + 1


def test_derive_project_honors_the_config_project_map(
        atelier_env: Dict, tmp_path: Path) -> None:
    """resolve_project's config-map layer must reach absorb too — the mapping
    that turns a folder into its real project identity."""
    import yaml
    real = tmp_path / "org" / "identity-hub"
    real.mkdir(parents=True)
    cfg_path = atelier_env["home"] / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data.setdefault("learnings", {})["project_map"] = {
        str(real): "org-identity-hub"}
    cfg_path.write_text(yaml.safe_dump(data))
    _ac.derive_project.cache_clear()
    assert _ac.derive_project(_encode(real)) == "org-identity-hub"


def test_unabsorbed_count_skips_project_resolution(
        atelier_env: Dict, monkeypatch) -> None:
    """The nudge count runs at EVERY session start and needs only body hashes.
    Resolving the project per file made it ~4 minutes on a real vault."""
    root = atelier_env["claude_projects"]
    _seed_claude(root, "-w-p1", "m1", type_="feedback", description="a")

    def _boom(*a, **k):
        raise AssertionError("unabsorbed_count must not resolve projects")

    monkeypatch.setattr(_ac, "derive_project", _boom)
    assert _ac.unabsorbed_count() == 1


# ── absorb ────────────────────────────────────────────────────────────────


def test_absorb_accepts_feedback_and_reference(atelier_env: Dict, tmp_path: Path) -> None:
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback",
                 description="don't mock the db")
    _seed_claude(src_root, "-w-lexio", "ref1", type_="reference",
                 description="dashboard URL")
    _seed_index(src_root, "-w-lexio")

    out = _ac.absorb(dry_run=False, source_root=src_root)
    assert len(out["accepted"]) == 2
    assert len(out["candidates"]) == 0
    # RFC 0007: absorbed memories are deterministically MINTED (generated_by
    # mint) from their own per-memory operational Source; accepted ones at
    # ac_status passed — NOT legacy notes/ files.
    from runtime.index.parse import split_frontmatter
    for item in out["accepted"]:
        assert "/graph/atomic/" in item["path"]   # flat L2 graph (P9.4)
        assert "/graph/atomic/claims/" not in item["path"]   # kind subdir gone
        assert "/learnings/notes/" not in item["path"]
        fm, _ = split_frontmatter(Path(item["path"]).read_text())
        assert fm["kind"] == "claim"
        assert fm["domain"] == "operational"
        # generated_by is the PROV activity; RFC 0007 mints deterministically.
        # The absorbed provenance stays on agent_kind/attributed_to.
        assert fm["generated_by"] == "mint"
        assert fm["agent_kind"] == "absorbed"
        assert fm["attributed_to"] == "absorbed"
        assert fm["ac_status"] == "passed"
        # RFC 0007: derives from the memory's OWN per-memory Source in
        # raw/operational/ — NOT the shared raw/inbox anchor.
        assert fm["derived_from"] and isinstance(fm["derived_from"], list)
        src_id = fm["derived_from"][0]
        srcs = [p for p in atelier_env["gorae"].rglob("*.md")
                if split_frontmatter(p.read_text())[0].get("entry_id") == src_id]
        assert len(srcs) == 1
        rel = srcs[0].relative_to(atelier_env["gorae"]).as_posix()
        assert rel.startswith("raw/operational/"), rel
        assert not rel.endswith("operational-capture.md"), rel


def test_absorb_routes_user_project_to_candidates(atelier_env: Dict,
                                                    tmp_path: Path) -> None:
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "u1", type_="user",
                 description="kyu is senior dev")
    _seed_claude(src_root, "-w-lexio", "p1", type_="project",
                 description="release freeze 2026-03-05")

    out = _ac.absorb(dry_run=False, source_root=src_root)
    assert len(out["accepted"]) == 0
    assert len(out["candidates"]) == 2


def test_absorb_dedupes_by_body_hash(atelier_env: Dict, tmp_path: Path) -> None:
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback")
    out1 = _ac.absorb(dry_run=False, source_root=src_root)
    assert len(out1["accepted"]) == 1
    # Run again — should be a no-op for the same body hash.
    out2 = _ac.absorb(dry_run=False, source_root=src_root)
    assert len(out2["accepted"]) == 0
    assert len(out2["deduped"]) == 1


def test_absorb_dry_run_writes_nothing(atelier_env: Dict, tmp_path: Path) -> None:
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback")
    vault = Path(_ac._vault_root())
    out = _ac.absorb(dry_run=True, source_root=src_root)
    assert len(out["accepted"]) == 1
    assert not (vault / "learnings" / "accepted" / "by-project" / "lexio").exists()


def test_absorbed_frontmatter_carries_source_metadata(atelier_env: Dict,
                                                       tmp_path: Path) -> None:
    src_root = tmp_path / "claude"
    src = _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback",
                       description="x")
    out = _ac.absorb(dry_run=False, source_root=src_root)
    accepted_path = Path(out["accepted"][0]["path"])
    from runtime.index.parse import split_frontmatter
    fm, _ = split_frontmatter(accepted_path.read_text(encoding="utf-8"))
    assert fm["source"] == "claude-memory"
    assert fm["claude_memory_type"] == "feedback"
    assert fm["source_path"] == str(src)
    assert fm["project_hint"] == "lexio"


def test_memory_md_index_is_skipped(atelier_env: Dict, tmp_path: Path) -> None:
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback")
    _seed_index(src_root, "-w-lexio")
    out = _ac.absorb(dry_run=False, source_root=src_root)
    assert len(out["accepted"]) == 1     # MEMORY.md not absorbed


def test_ledger_is_single_vault_root_json_file(atelier_env: Dict,
                                                tmp_path: Path) -> None:
    """The dedup ledger is ONE JSON file at the vault root (keyed by body sha),
    not a directory of per-hash files, and not under a content lane."""
    import json
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback")
    vault = Path(_ac._vault_root())
    _ac.absorb(dry_run=False, source_root=src_root)

    ledger = vault / ".absorbed-from-claude.json"
    assert ledger.is_file()                                   # single file …
    assert not (vault / "raw" / "learning").exists()          # … no legacy dir
    data = json.loads(ledger.read_text(encoding="utf-8"))
    # RFC 0008 M2: the indexed shape — dedup by body sha, plus a
    # machine-independent path index that makes "same file, new hash" mean
    # "revised" rather than "new memory".
    assert set(data) == {"by_sha", "by_path"}
    assert len(data["by_sha"]) == 1
    (sha, rec), = data["by_sha"].items()
    # the stale, unused `dest` field is dropped; claim_id + statement are
    # recorded so a later revision can supersede this claim and locate its
    # file in O(1)
    assert set(rec) == {"source_path", "absorbed_at", "project", "type",
                        "claim_id", "statement"}
    # the path key carries NO absolute path (the ledger is git-tracked and
    # must resolve on another machine)
    (key, indexed_sha), = data["by_path"].items()
    assert key == "-w-lexio/fb1.md" and indexed_sha == sha
    assert not key.startswith("/")


def test_dry_run_writes_no_ledger(atelier_env: Dict, tmp_path: Path) -> None:
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback")
    vault = Path(_ac._vault_root())
    _ac.absorb(dry_run=True, source_root=src_root)
    assert not (vault / ".absorbed-from-claude.json").exists()


def test_corrupt_ledger_is_tolerated(atelier_env: Dict, tmp_path: Path) -> None:
    """A present-but-corrupt ledger must not crash absorb; it is treated as empty
    (and warned — see _load_ledger), the memory is imported, and the ledger is
    rewritten as a valid dict."""
    import json
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback")
    vault = Path(_ac._vault_root())
    (vault / ".absorbed-from-claude.json").write_text("{ not json",
                                                       encoding="utf-8")
    out = _ac.absorb(dry_run=False, source_root=src_root)
    assert len(out["accepted"]) == 1                          # tolerated, imported
    data = json.loads((vault / ".absorbed-from-claude.json").read_text())
    assert set(data) == {"by_sha", "by_path"}                 # rewritten valid
    assert len(data["by_sha"]) == 1


def test_mcp_dispatch_absorb_claude_memory(atelier_env: Dict, tmp_path: Path) -> None:
    src_root = tmp_path / "claude"
    _seed_claude(src_root, "-w-lexio", "fb1", type_="feedback")
    from runtime.service import tools as _tools

    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_absorb_claude_memory",
            dry_run=False, source_root=str(src_root),
        )

    out = asyncio.run(go())
    assert len(out["accepted"]) == 1
