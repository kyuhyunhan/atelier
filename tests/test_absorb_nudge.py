"""RFC 0008 M1 + M4 — the absorb nudge and the safety boundary.

M1: an *unabsorbed* memory is a derived state — a `~/.claude` memory whose
normalized body sha256 is not in the vault dedup ledger. The nudge mirrors
atomize/dream (`{due, count, short, long}`, human-pulled, never cron).

M4: safety at the absorb boundary, demote-never-block — a `type: user` memory
lands `sensitivity: private`; a PII pattern hit (same pattern file as the
pre-commit guard) demotes to private and flags `pii_flag: true` on Claim AND
Source; an absent pattern file is a no-op.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml

from runtime.index.parse import split_frontmatter
from runtime.service import nudges as _nudges
from runtime.service.learnings import absorb_claude as _ac
from runtime.service.learnings import bootstrap as _bs
from runtime.structure import resolver as _structure


def _seed_claude(root: Path, project_dir: str, name: str, *,
                 type_: str, description: str = "",
                 body: str | None = None) -> Path:
    p = root / project_dir / "memory" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\ntype: {type_}\n---\n"
    real_body = body if body is not None else (
        f"## Rule\nrule for {name}: be explicit.\n")
    p.write_text(fm + real_body, encoding="utf-8")
    return p


def _croot(atelier_env: Dict) -> Path:
    return atelier_env["claude_projects"]


def _set_absorb_cfg(home: Path, *, after: int) -> None:
    cfg_path = home / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data.setdefault("learnings", {})["absorb"] = {"nudge_after_memories": after}
    cfg_path.write_text(yaml.safe_dump(data))


def _source_fms(vault: Path):
    d = vault / _structure.operational_source_dir()
    for p in sorted(d.glob("*.md")) if d.exists() else []:
        fm, _ = split_frontmatter(p.read_text(encoding="utf-8"))
        yield fm


# ── M1: unabsorbed_count ─────────────────────────────────────────────────────


def test_count_zero_when_no_memories(atelier_env: Dict) -> None:
    assert _ac.unabsorbed_count() == 0


def test_count_unledgered_and_skips_index(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "m1", type_="feedback", description="a")
    _seed_claude(root, "-w-p1", "m2", type_="user", description="b")
    (root / "-w-p1" / "memory" / "MEMORY.md").write_text("# index\n")
    assert _ac.unabsorbed_count() == 2


def test_count_drops_after_absorb(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "m1", type_="feedback", description="a")
    assert _ac.unabsorbed_count() == 1
    _ac.absorb(dry_run=False, source_root=root)
    assert _ac.unabsorbed_count() == 0
    # and the nudge goes quiet
    assert _ac.nudge_info()["due"] is False


# ── M1: nudge_info ───────────────────────────────────────────────────────────


def test_nudge_not_due_when_empty(atelier_env: Dict) -> None:
    info = _ac.nudge_info()
    assert info == {"due": False, "count": 0, "short": "", "long": ""}


def test_nudge_due_with_backlog(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "m1", type_="feedback", description="a")
    _seed_claude(root, "-w-p1", "m2", type_="project", description="b")
    info = _ac.nudge_info()
    assert info["due"] is True and info["count"] == 2
    assert "2 Claude Code memories" in info["long"]
    assert "atelier_absorb_claude_memory" in info["long"]
    assert "copy-only" in info["long"]           # the posture, stated
    assert "2 to absorb" in info["short"]


def test_nudge_singular_noun(atelier_env: Dict) -> None:
    _seed_claude(_croot(atelier_env), "-w-p1", "m1", type_="feedback",
                 description="a")
    long = _ac.nudge_info()["long"]
    assert "1 Claude Code memory " in long and "memories" not in long


def test_nudge_threshold_respected(atelier_env: Dict) -> None:
    _set_absorb_cfg(atelier_env["home"], after=3)
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "m1", type_="feedback", description="a")
    _seed_claude(root, "-w-p1", "m2", type_="feedback", description="b")
    assert _ac.nudge_info()["due"] is False      # 2 < 3
    _seed_claude(root, "-w-p1", "m3", type_="feedback", description="c")
    assert _ac.nudge_info()["due"] is True


# ── M1: unified nudge surface + bootstrap ────────────────────────────────────


def test_all_nudges_absorb_first(atelier_env: Dict) -> None:
    _seed_claude(_croot(atelier_env), "-w-p1", "m1", type_="feedback",
                 description="a")
    by_kind = {n.kind: n for n in
               _nudges.all_nudges(now="2026-07-22T12:00:00+00:00")}
    a = by_kind["absorb"]
    assert a.due is True and a.count == 1
    assert "atelier absorb" in a.long and a.short


def test_bootstrap_surfaces_absorb_nudge(atelier_env: Dict) -> None:
    _seed_claude(_croot(atelier_env), "-w-p1", "m1", type_="feedback",
                 description="a")
    out = _bs.bootstrap(working_dir=None, now="2026-07-22T12:00:00+00:00")
    assert out["absorb_nudge"] is True
    assert "atelier absorb" in out["markdown"]


def test_bootstrap_quiet_when_nothing_to_absorb(atelier_env: Dict) -> None:
    out = _bs.bootstrap(working_dir=None, now="2026-07-22T12:00:00+00:00")
    assert out["absorb_nudge"] is False
    assert "atelier absorb" not in out["markdown"]


# ── M4: sensitivity defaults ─────────────────────────────────────────────────


def test_user_memory_lands_private(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "who", type_="user",
                 description="the user prefers terse answers")
    out = _ac.absorb(dry_run=False, source_root=root)
    assert len(out["candidates"]) == 1           # user stays pending
    fm, _ = split_frontmatter(Path(out["candidates"][0]["path"]).read_text())
    assert fm["sensitivity"] == "private"
    assert "pii_flag" not in fm                  # private by TYPE, not by PII
    src_fms = list(_source_fms(atelier_env["gorae"]))
    assert len(src_fms) == 1 and src_fms[0]["sensitivity"] == "private"


def test_feedback_memory_stays_public(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "fb", type_="feedback",
                 description="never mock the db in integration tests")
    out = _ac.absorb(dry_run=False, source_root=root)
    fm, _ = split_frontmatter(Path(out["accepted"][0]["path"]).read_text())
    assert fm["sensitivity"] == "public"


# ── M4: PII demotion (demote-never-block) ────────────────────────────────────


def test_pii_hit_demotes_and_flags(atelier_env: Dict) -> None:
    (atelier_env["home"] / "pii_patterns.txt").write_text(
        "# personal names\nSECRETNAME\n", encoding="utf-8")
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "leaky", type_="feedback",
                 description="deploy checklist",
                 body="ask SECRETNAME before deploying\n")
    out = _ac.absorb(dry_run=False, source_root=root)
    assert len(out["accepted"]) == 1             # minted, never blocked
    fm, _ = split_frontmatter(Path(out["accepted"][0]["path"]).read_text())
    assert fm["sensitivity"] == "private"
    assert fm["pii_flag"] is True
    src_fms = list(_source_fms(atelier_env["gorae"]))
    assert src_fms[0]["sensitivity"] == "private"
    assert src_fms[0]["pii_flag"] is True


def test_no_pattern_file_is_noop(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "clean", type_="feedback",
                 description="a rule", body="nothing sensitive here\n")
    out = _ac.absorb(dry_run=False, source_root=root)
    fm, _ = split_frontmatter(Path(out["accepted"][0]["path"]).read_text())
    assert fm["sensitivity"] == "public"
    assert "pii_flag" not in fm


def test_pattern_file_bad_lines_skipped_good_lines_applied(
        atelier_env: Dict) -> None:
    """An uncompilable regex and a POSIX class are skipped (warned, not
    silent), but the remaining valid patterns still enforce."""
    (atelier_env["home"] / "pii_patterns.txt").write_text(
        "([unclosed\n[[:alpha:]]name\nSECRETNAME\n", encoding="utf-8")
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "leaky", type_="feedback",
                 description="checklist", body="ping SECRETNAME first\n")
    out = _ac.absorb(dry_run=False, source_root=root)
    fm, _ = split_frontmatter(Path(out["accepted"][0]["path"]).read_text())
    assert fm["sensitivity"] == "private" and fm["pii_flag"] is True


def test_dry_run_previews_sensitivity(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "who", type_="user", description="a user fact")
    out = _ac.absorb(dry_run=True, source_root=root)
    assert out["candidates"][0]["sensitivity"] == "private"


def test_demoted_absorb_still_dedupes_on_rerun(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed_claude(root, "-w-p1", "who", type_="user", description="a user fact")
    _ac.absorb(dry_run=False, source_root=root)
    out2 = _ac.absorb(dry_run=False, source_root=root)
    assert len(out2["deduped"]) == 1
    assert out2["accepted"] == [] and out2["candidates"] == []


# ── RFC 0008 M2 gap: a revision that keeps the description is dropped ─────────


def test_revision_with_same_description_is_flagged_not_silent(
        atelier_env: Dict) -> None:
    """Until M2 supersession lands, an upstream body edit that keeps the
    description mints onto the SAME content-addressed nodes — both now
    idempotent — so the revised body is stored nowhere. The ledger records the
    new hash regardless, so the operator must be TOLD."""
    root = _croot(atelier_env)
    p = _seed_claude(root, "-w-p1", "m1", type_="feedback",
                     description="one durable rule", body="v1 body\n")
    first = _ac.absorb(dry_run=False, source_root=root)
    assert "revision_dropped" not in first["accepted"][0]

    # same description, different body → new sha (not deduped), same claim id
    p.write_text(p.read_text().replace("v1 body", "v2 revised body"),
                 encoding="utf-8")
    second = _ac.absorb(dry_run=False, source_root=root)
    assert second["deduped"] == []                  # a genuinely new hash
    assert second["accepted"][0]["revision_dropped"] is True
    assert second["accepted"][0]["path"] == first["accepted"][0]["path"]
