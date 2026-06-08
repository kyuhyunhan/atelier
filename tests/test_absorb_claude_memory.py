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
        "-Users-kyuhyunhan-workspaces-lexio"
    ) == "/Users/kyuhyunhan/workspaces/lexio"


def test_derive_project_takes_basename() -> None:
    assert _ac.derive_project("-Users-kyuhyunhan-workspaces-lexio") == "lexio"


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
    # RFC 0001: both land as flat notes under notes/<YYYY-MM>/, no mirror.
    for item in out["accepted"]:
        assert "/learnings/notes/" in item["path"]
        assert "by-topic" not in item["path"] and "by-project" not in item["path"]


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
