"""PR-30: proposed status, atomic writes, evidence-overlap idempotent dedup."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest

from runtime.service.learnings import bootstrap as _bs
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import review as _rev


def _read_fm(path: Path) -> Dict:
    from runtime.index.parse import split_frontmatter
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def _accept(project: str, topic: str, seed: str) -> tuple[str, str]:
    """Return (slug, entry_id) of an accepted learning."""
    cap = _cap.capture(
        observation=f"obs {seed} in {project}",
        why=f"why {seed}", rule=f"rule {seed}",
        working_dir=f"/Users/me/workspaces/{project}",
        session_id=seed, hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic=topic, target_project=project)
    return Path(out["path"]).stem, cap["entry_id"]


# ── proposed status ─────────────────────────────────────────────────────────


def test_synthesize_defaults_to_proposed(atelier_env: Dict) -> None:
    s1, e1 = _accept("lexio", "testing", "a")
    s2, e2 = _accept("bht", "testing", "b")
    out = _pr.synthesize(source_slugs=[s1, s2],
                         title="real db over mocks",
                         source_entry_ids=[e1, e2])
    assert out["skipped"] is False
    assert out["status"] == "proposed"
    fm = _read_fm(Path(out["path"]))
    assert fm["status"] == "proposed"
    assert fm["ac_status"] == "pending"
    assert "proposed_at" in fm
    assert "accepted_at" not in fm
    assert fm["source_entry_ids"] == [e1, e2]


def test_add_accepted_still_default(atelier_env: Dict) -> None:
    out = _pr.add(title="manual one", rule="r", why="w")
    fm = _read_fm(Path(out["path"]))
    assert fm["status"] == "accepted"
    assert fm["ac_status"] == "passed"
    assert "accepted_at" in fm


def test_add_rejects_archived_status(atelier_env: Dict) -> None:
    with pytest.raises(ValueError, match="proposed|accepted"):
        _pr.add(title="x", rule="r", why="w", status="archived")


# ── proposed NOT injected at session start ──────────────────────────────────


def test_proposed_principle_not_injected_by_bootstrap(atelier_env: Dict) -> None:
    # A proposed always-inject principle must NOT appear in bootstrap.
    s1, e1 = _accept("lexio", "testing", "a")
    s2, e2 = _accept("bht", "testing", "b")
    _pr.synthesize(source_slugs=[s1, s2], title="proposed rule",
                   priority="always-inject", source_entry_ids=[e1, e2],
                   slug="proposed-rule")
    # And an accepted one that SHOULD appear.
    _pr.add(title="accepted rule", rule="r", why="w",
            priority="always-inject", slug="accepted-rule")

    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio")
    md = out["markdown"]
    assert "accepted rule" in md
    assert "proposed rule" not in md


# ── atomic write (no .tmp left behind) ──────────────────────────────────────


def test_atomic_write_leaves_no_tmp(atelier_env: Dict) -> None:
    out = _pr.add(title="atomic check", rule="r", why="w", slug="atomic-check")
    pdir = Path(out["path"]).parent
    tmps = list(pdir.glob(".*.tmp"))
    assert tmps == []
    assert Path(out["path"]).exists()


# ── idempotent dedup ────────────────────────────────────────────────────────


def test_synthesize_skips_when_already_covered(atelier_env: Dict) -> None:
    s1, e1 = _accept("lexio", "testing", "a")
    s2, e2 = _accept("bht", "testing", "b")
    first = _pr.synthesize(source_slugs=[s1, s2], title="rule one",
                           source_entry_ids=[e1, e2], slug="rule-one")
    assert first["skipped"] is False
    # Re-run with the same members → skipped (already covered, proposed).
    second = _pr.synthesize(source_slugs=[s1, s2], title="rule one again",
                            source_entry_ids=[e1, e2], slug="rule-one-again")
    assert second["skipped"] is True
    assert second["reason"] == "already-covered"
    assert second["covered_by"]["status"] == "proposed"


def test_dedup_checks_archived_so_rejected_not_reproposed(atelier_env: Dict) -> None:
    s1, e1 = _accept("lexio", "testing", "a")
    s2, e2 = _accept("bht", "testing", "b")
    out = _pr.synthesize(source_slugs=[s1, s2], title="rejected rule",
                         source_entry_ids=[e1, e2], slug="rejected-rule")
    # User rejects it → archived.
    _pr.archive(slug="rejected-rule", reason="not a real principle")
    # Next dream re-clusters the same members → must be skipped (archived).
    again = _pr.synthesize(source_slugs=[s1, s2], title="rejected rule v2",
                           source_entry_ids=[e1, e2], slug="rejected-rule-v2")
    assert again["skipped"] is True
    assert again["covered_by"]["status"] == "archived"


def test_dedup_partial_overlap_below_threshold_not_skipped(atelier_env: Dict) -> None:
    s1, e1 = _accept("lexio", "testing", "a")
    s2, e2 = _accept("bht", "testing", "b")
    s3, e3 = _accept("ich", "testing", "c")
    _pr.synthesize(source_slugs=[s1], title="rule a only",
                   source_entry_ids=[e1], slug="rule-a")
    # New cluster shares only e1 of {e1,e2,e3} = 1/3 ≈ 0.33 < 0.6 → not skipped.
    out = _pr.synthesize(source_slugs=[s1, s2, s3], title="bigger rule",
                         source_entry_ids=[e1, e2, e3], slug="bigger-rule",
                         overlap_threshold=0.6)
    assert out["skipped"] is False


def test_find_covering_principle_direct(atelier_env: Dict) -> None:
    s1, e1 = _accept("lexio", "testing", "a")
    s2, e2 = _accept("bht", "testing", "b")
    _pr.synthesize(source_slugs=[s1, s2], title="cov",
                   source_entry_ids=[e1, e2], slug="cov")
    hit = _pr.find_covering_principle([e1, e2], overlap_threshold=0.6)
    assert hit is not None and hit["slug"] == "cov"
    miss = _pr.find_covering_principle(["unrelated-id"], overlap_threshold=0.6)
    assert miss is None
