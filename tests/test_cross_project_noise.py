"""RFC 0009 §5.4 — `cross_project_noise{project, foreign_ratio, returned}`.

The metric that makes pillar-③'s missing axis measurable: run the PRODUCTION
dev-session recall (`rank_claims(..., tier='proactive', lens='dev')`) for the
fixture's project and report the fraction of returned claims belonging to some
OTHER project. Three §5.4 signals, pinned here:

- fixture absent / shapeless → the metric returns None (key omitted — a fresh
  clone's CI neither fails nor reports green);
- too few hits (returned < 20) → `returned` present, `foreign_ratio` OMITTED
  (fixer-addressable FAIL, not a harness raise);
- enough hits → `foreign_ratio` present, a real measurement.

The natural-but-wrong encodings (`{"returned": 0, "foreign_ratio": 0.0}`) pass
a `≤ 0.15` bound on a lens that returns nothing — §5.4 forbids exactly that.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from runtime.service import api as _api
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import metrics as _metrics


def _write_fixture(path: Path, *, project: str = "projA",
                   probes=("alpha beta gamma",)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "project": project,
                                "probes": list(probes)}), encoding="utf-8")


def _seed_claims(vault: Path, *, own: int, foreign: int) -> None:
    """Recallable proactive operational claims: `own` for projA, `foreign`
    spread over other projects. Statements share the probe tokens so the
    production recall path actually returns them."""
    import uuid
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(own + foreign):
        proj = "projA" if i < own else f"projB{i % 3}"
        eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"noise-{i}"))
        (d / f"noise-{i}.md").write_text(
            f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
            f"domain: operational\nsensitivity: public\nsurfacing: proactive\n"
            f"ac_status: passed\nproject: {proj}\n"
            f"created_at: 2026-07-01T00:00:00+00:00\n"
            f"statement: alpha beta gamma finding number {i}\n---\n\n"
            f"alpha beta gamma body {i}\n",
            encoding="utf-8")
    _api.reindex(space="gorae", full=True)


def test_abstains_without_a_fixture(tmp_path: Path) -> None:
    assert _metrics.cross_project_noise(
        fixture_path=tmp_path / "nope.json") is None


def test_abstains_on_a_shapeless_fixture(tmp_path: Path) -> None:
    """Present-but-shapeless is corruption, not an empty measurement — the
    same rule the dangling baseline enforces (a truncated write must not
    fabricate a verdict)."""
    for body in ("", "[]", "{}", '{"project": "p"}', '{"probes": []}',
                 '{"project": "", "probes": ["q"]}', "not json"):
        p = tmp_path / "probes.json"
        p.write_text(body, encoding="utf-8")
        assert _metrics.cross_project_noise(fixture_path=p) is None, repr(body)


def test_low_yield_reports_returned_but_omits_ratio(
        atelier_env: Dict, tmp_path: Path) -> None:
    """§5.4's middle row: a lens change that under-delivers is a FAIL the fixer
    can address — `returned` present, `foreign_ratio` absent — never a raise,
    and never a fabricated 0.0 that would pass the bound."""
    vault = Path(_cl._vault_root())
    _seed_claims(vault, own=2, foreign=3)          # 5 << 20
    fx = tmp_path / "probes.json"
    _write_fixture(fx)
    got = _metrics.cross_project_noise(fixture_path=fx)
    assert got is not None
    assert got["returned"] == 5
    assert "foreign_ratio" not in got


def test_measures_foreign_ratio_at_yield(atelier_env: Dict,
                                         tmp_path: Path) -> None:
    """The real measurement, through the PRODUCTION dev-recall path: 25
    recallable claims, 20 foreign → ratio 0.8."""
    vault = Path(_cl._vault_root())
    _seed_claims(vault, own=5, foreign=20)
    fx = tmp_path / "probes.json"
    _write_fixture(fx)
    got = _metrics.cross_project_noise(fixture_path=fx)
    assert got is not None
    assert got["returned"] == 25
    assert got["project"] == "projA"
    assert abs(got["foreign_ratio"] - 0.8) < 1e-9


def test_metrics_block_omits_the_key_without_a_fixture(
        atelier_env: Dict, tmp_path: Path, monkeypatch) -> None:
    """CI on a fresh clone: no fixture → no key (the §5.6 posture), not a
    green zero."""
    import datetime
    monkeypatch.setattr(_metrics, "_PROBES_PATH", tmp_path / "absent.json")
    out = _metrics.metrics(as_of=datetime.date(2026, 7, 29),
                           vault=Path(_cl._vault_root()))
    assert "cross_project_noise" not in out
