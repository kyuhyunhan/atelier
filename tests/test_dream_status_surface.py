"""PR-35: nudge_info (shared decision) + `atelier dream --status` surface."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest
import yaml

from runtime.cli import main as cli_main
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import dream as _dr
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
    cap = _cap.capture(observation=f"obs {seed}", why=f"why {seed}",
                       rule=f"rule {seed}",
                       working_dir=f"/Users/me/workspaces/{project}",
                       session_id=seed, hook="Stop")
    out = _rev.accept(candidate_slug=cap["entry_id"],
                      target_topic="t", target_project=project)
    # The dream cadence counts the PROACTIVE pool (dream's input) — elevate the
    # accepted claim to proactive so it counts toward the nudge threshold.
    from runtime.service.learnings import claims_io as _ci
    found = _ci.find_claim_by_entry_id(cap["entry_id"])
    if found is not None:
        p, fm, body = found
        _ci.set_surfacing(p, fm, body, new_tier=_ci.TIER_PROACTIVE,
                          generated_by="promote")
    return cap["entry_id"]


# ── nudge_info shared decision ──────────────────────────────────────────────


def test_nudge_info_not_due_below_threshold(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=15, after_days=7)
    _accept("a")
    info = _dr.nudge_info(now="2026-05-29T12:00:00+00:00")
    assert info["due"] is False
    assert info["short"] == ""
    assert info["long"] == ""


def test_nudge_info_due_on_count(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=2, after_days=7)
    _accept("a"); _accept("b")
    info = _dr.nudge_info(now="2026-05-29T12:00:00+00:00")
    assert info["due"] is True
    assert info["proactive_since"] == 2
    assert "2 to dream" in info["short"]
    assert "💡 **atelier dream**" in info["long"]


def test_nudge_info_short_and_long_consistent_with_bootstrap(atelier_env: Dict) -> None:
    """bootstrap's model-context nudge must equal nudge_info.long."""
    from runtime.service.learnings import bootstrap as _bs
    _set_dream_cfg(atelier_env["home"], after_accepted=1, after_days=7)
    _accept("a")
    info = _dr.nudge_info(now="2026-05-29T12:00:00+00:00")
    out = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio",
                        now="2026-05-29T12:00:00+00:00")
    assert info["long"] in out["markdown"]


def test_nudge_info_pending_segment(atelier_env: Dict) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=999, after_days=999)
    _pr.add(title="draft", rule="r", why="w", status="proposed", slug="d")
    info = _dr.nudge_info(now="2026-05-29T12:00:00+00:00")
    assert info["due"] is True
    assert info["pending"] == 1
    assert "to review" in info["short"]
    assert "await review" in info["long"]


# ── CLI `atelier dream --status` ────────────────────────────────────────────


def test_cli_status_compact_line(atelier_env: Dict, capsys) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=2, after_days=7)
    _accept("a"); _accept("b")
    rc = cli_main(["dream", "--status"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("💡 atelier:")
    assert "to dream" in out


def test_cli_status_empty_when_nothing_due(atelier_env: Dict, capsys) -> None:
    _set_dream_cfg(atelier_env["home"], after_accepted=99, after_days=99)
    _accept("a")
    rc = cli_main(["dream", "--status"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == ""          # statusline shows nothing extra


def test_cli_status_json(atelier_env: Dict, capsys) -> None:
    import json
    _set_dream_cfg(atelier_env["home"], after_accepted=2, after_days=7)
    _accept("a"); _accept("b")
    rc = cli_main(["dream", "--status", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["due"] is True
    assert data["proactive_since"] == 2
    assert "long" in data and "short" in data
