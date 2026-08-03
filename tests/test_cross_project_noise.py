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
    _api.reindex(space="wiki", full=True)


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
        atelier_env: dict, tmp_path: Path) -> None:
    """§5.4's middle row: a lens change that under-delivers is a FAIL the fixer
    can address — `returned` present, `foreign_ratio` absent — never a raise,
    and never a fabricated 0.0 that would pass the bound. Since G3, the
    production path also GATES foreign-owned claims at the push tier, so only
    the 2 own claims come back (the 3 foreign seeds are scoped out)."""
    vault = Path(_cl._vault_root())
    _seed_claims(vault, own=2, foreign=3)          # 2 admitted << 20
    fx = tmp_path / "probes.json"
    _write_fixture(fx)
    got = _metrics.cross_project_noise(fixture_path=fx)
    assert got is not None
    assert got["returned"] == 2
    assert "foreign_ratio" not in got


def test_measures_composition_at_yield_with_the_g3_gate(
        atelier_env: dict, tmp_path: Path) -> None:
    """The real measurement, through the PRODUCTION dev-recall path. G3's
    project-scope gate removes foreign-owned claims from the push tier, so a
    corpus with 20 foreign seeds measures `foreign: 0` — the metric proves the
    gate through the same path a session serves, not around it."""
    vault = Path(_cl._vault_root())
    _seed_claims(vault, own=25, foreign=20)
    fx = tmp_path / "probes.json"
    _write_fixture(fx)
    got = _metrics.cross_project_noise(fixture_path=fx)
    assert got is not None
    assert got["returned"] == 25
    assert got["project"] == "projA"
    assert abs(got["foreign_ratio"] - 0.0) < 1e-9
    # the composition leaves (review MUST): the absolutes a contract pins so
    # the ratio cannot be satisfied by displacement alone
    assert (got["own"], got["foreign"], got["unowned"]) == (25, 0, 0)


def test_unowned_dilution_is_visible_in_the_composition(
        atelier_env: dict, tmp_path: Path) -> None:
    """The dilution vector, pinned: project-less (knowledge) claims lower the
    RATIO without serving one more own-project claim. The composition leaves
    expose it — `own` stays flat while `unowned` absorbs the denominator, so
    a contract binding `own`/`foreign` absolutes catches what the ratio alone
    would wave through."""
    import uuid
    vault = Path(_cl._vault_root())
    _seed_claims(vault, own=5, foreign=8)     # the 8 foreign are gated (G3)
    d = vault / "graph" / "atomic"
    for i in range(20):                       # flood with unowned knowledge
        eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"kn-{i}"))
        (d / f"kn-{i}.md").write_text(
            f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
            f"domain: knowledge\nsensitivity: public\nsurfacing: proactive\n"
            f"created_at: 2026-07-01T00:00:00+00:00\n"
            f"statement: alpha beta gamma insight {i}\n---\n\n"
            f"alpha beta gamma body {i}\n",
            encoding="utf-8")
    _api.reindex(space="wiki", full=True)
    fx = tmp_path / "probes.json"
    _write_fixture(fx)
    got = _metrics.cross_project_noise(fixture_path=fx)
    assert got is not None and got["returned"] == 25
    assert abs(got["foreign_ratio"] - 0.0) < 1e-9      # foreign gated out (G3)
    assert got["own"] == 5                             # …but own did NOT rise
    assert got["unowned"] == 20                        # the dilution, visible


def test_metrics_block_omits_the_key_without_a_fixture(
        atelier_env: dict, tmp_path: Path, monkeypatch) -> None:
    """CI on a fresh clone: no fixture → no key (the §5.6 posture), not a
    green zero."""
    import datetime
    monkeypatch.setattr(_metrics, "_PROBES_PATH", tmp_path / "absent.json")
    out = _metrics.metrics(as_of=datetime.date(2026, 7, 29),
                           vault=Path(_cl._vault_root()))
    assert "cross_project_noise" not in out
