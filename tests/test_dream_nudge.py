"""PR-32: session-start dream nudge (threshold + pending-review + interrupted)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest
import yaml

from runtime.service.learnings import bootstrap as _bs
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import principles as _pr
from runtime.service.learnings import review as _rev


def _set_dream_cfg(home: Path, *, after_accepted: int, after_days: int) -> None:
    cfg_path = home / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data.setdefault("learnings", {})["dream"] = {
        "nudge_after_accepted": after_accepted,
        "nudge_after_days": after_days,
    }
    cfg_path.write_text(yaml.safe_dump(data))


def _accept(seed: str, project: str = "lexio") -> str:
    cap = _cap.capture(
        observation=f"obs {seed}", why=f"why {seed}", rule=f"rule {seed}",
        working_dir=f"/Users/me/workspaces/{project}",
        session_id=seed, hook="Stop",
    )
    out = _rev.accept(candidate_slug=cap["entry_id"],
                       target_topic="t", target_project=project)
    # The dream cadence counts the PROACTIVE pool (dream's input), not accepted
    # learnings — so elevate the accepted claim to proactive to make it count.
    from runtime.service.learnings import claims_io as _ci
    found = _ci.find_claim_by_entry_id(cap["entry_id"])
    if found is not None:
        p, fm, body = found
        _ci.set_surfacing(p, fm, body, new_tier=_ci.TIER_PROACTIVE,
                          generated_by="promote")
    return cap["entry_id"]


# ── no nudge when below threshold ───────────────────────────────────────────


def test_no_nudge_below_threshold(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=15, after_days=7)
    _accept("a")
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                        now="2026-05-28T12:00:00+00:00")
    assert out["nudge"] is False
    assert "atelier dream" not in out["markdown"]


# ── count threshold ─────────────────────────────────────────────────────────


def test_nudge_on_count_threshold(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=2, after_days=7)
    _accept("a"); _accept("b")
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                        now="2026-05-28T12:00:00+00:00")
    assert out["nudge"] is True
    assert "atelier dream" in out["markdown"]
    assert "new proactive claims" in out["markdown"]


# ── days threshold ──────────────────────────────────────────────────────────


def test_nudge_on_days_threshold(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=999, after_days=7)
    _accept("a")
    # Mark a dream 10 days before "now" so the days trigger fires while the
    # count trigger (999) does not.
    _cl.mark_dream_complete(when="2026-05-18T12:00:00+00:00")
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                        now="2026-05-28T12:00:00+00:00")
    assert out["nudge"] is True
    assert "days since the last dream" in out["markdown"]


def test_no_nudge_when_recent_dream_and_low_count(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=999, after_days=7)
    _accept("a")
    _cl.mark_dream_complete(when="2026-05-27T12:00:00+00:00")  # 1 day ago
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                        now="2026-05-28T12:00:00+00:00")
    assert out["nudge"] is False


# ── pending proposed drafts always nudge ────────────────────────────────────


def test_nudge_on_pending_proposed(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=999, after_days=999)
    e1 = _accept("a", project="lexio")
    e2 = _accept("b", project="bht")
    # A proposed draft exists but thresholds are huge → nudge still fires
    # because of the pending-review trigger.
    _pr.synthesize(source_slugs=[], source_entry_ids=[e1, e2],
                   title="x", slug="d1", skip_if_covered=False) if False else None
    # Use add(status=proposed) directly to avoid needing slug resolution.
    _pr.add(title="pending draft", rule="r", why="w",
            status="proposed", slug="pending-draft",
            source_entry_ids=[e1, e2])
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                        now="2026-05-28T12:00:00+00:00")
    assert out["nudge"] is True
    assert "await review" in out["markdown"]


# ── interrupted dream stays armed (last_dream_at not advanced) ───────────────


def test_interrupted_dream_keeps_nudge_armed(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=2, after_days=7)
    _accept("a"); _accept("b"); _accept("c")
    # Simulate an interrupted pass: a proposed draft was written but
    # mark_dream_complete was NEVER called (last_dream_at stays None).
    _pr.add(title="half done", rule="r", why="w", status="proposed",
            slug="half-done")
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                        now="2026-05-28T12:00:00+00:00")
    # Both triggers fire: accumulation (3 >= 2) AND pending review.
    assert out["nudge"] is True
    md = out["markdown"]
    assert "await review" in md
    assert "since the last dream" in md


# ── nudge sits above the rest of the bootstrap block ────────────────────────


def test_nudge_is_first_line(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=1, after_days=7)
    _accept("a")
    _pr.add(title="always one", rule="r", why="w",
            priority="always-inject", slug="always-one")
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                        now="2026-05-28T12:00:00+00:00")
    assert out["markdown"].lstrip().startswith("💡 **atelier dream**")
