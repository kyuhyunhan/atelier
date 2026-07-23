"""RFC 0009 §5 — the goal-program metrics.

The tests that matter here are the ones that would FAIL against a plausible
wrong implementation, because every counter in this module is authored by the
same change it grades (§3.2). Two in particular:

- `test_promote_eligible_matches_the_production_predicate` fails if the counter
  is re-implemented rather than wrapped — including the specific attack §3.2
  names, dropping the born-accepted branch so knowledge claims stop counting.
- `test_lens_coverage_cannot_be_claimed_by_the_schema_file` fails if the
  numerator is ever taken from the declaration instead of the live handler
  signature, which is what makes "6 of 6" unfakeable.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Dict

from runtime.service.learnings import claims_io as _claims
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import metrics as _metrics
from runtime.promote import propose as _propose
from runtime.service import api as _api


def _write_claim(vault: Path, name: str, *, domain: str, sensitivity: str,
                 ac_status: str = "", surfacing: str = "query",
                 created_at: str = "2026-07-01T00:00:00+00:00") -> None:
    import uuid
    eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claim-{name}"))
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    ac = f"ac_status: {ac_status}\n" if ac_status else ""
    (d / f"{name}.md").write_text(
        f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
        f"domain: {domain}\nsensitivity: {sensitivity}\nsurfacing: {surfacing}\n"
        f"{ac}created_at: {created_at}\nstatement: statement of {name}\n---\n\nbody\n",
        encoding="utf-8")


# ── 5.1 promote eligibility ─────────────────────────────────────────────────

def test_promote_eligible_matches_the_production_predicate(
        atelier_env: Dict) -> None:
    """§3.2 rule 1 + §11.2: the counter must EQUAL the path the feature uses.

    This is the divergence test. The attack it blocks: implement the counter as
    `ac_status == 'passed'` only, dropping the born-accepted branch. That
    reports a small number and passes every contract clause on a vault whose
    promote proposal is still enormous.
    """
    vault = Path(_cl._vault_root())
    _write_claim(vault, "born-accepted", domain="knowledge", sensitivity="public")
    _write_claim(vault, "reviewed", domain="operational", sensitivity="public",
                 ac_status="passed")
    _write_claim(vault, "private-one", domain="knowledge", sensitivity="private")
    _write_claim(vault, "not-query", domain="knowledge", sensitivity="public",
                 surfacing="proactive")
    _api.reindex(space="gorae", full=True)

    got = _metrics.promote_eligible(vault=vault)
    assert got["total"] == len(_propose._eligible(limit=10_000))
    # and the born-accepted branch is genuinely counted, not silently dropped
    assert got["by_domain"] == {"knowledge": 1, "operational": 1}


def test_promote_eligible_falls_back_on_a_cold_db(atelier_env: Dict) -> None:
    """§5.1: `projection_counts` answers None on a cold DB, and under the
    abstain rule a None would become key-absence and abort the run. The counter
    must route through the filesystem fallback instead."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "one", domain="knowledge", sensitivity="public")
    # deliberately NOT reindexed — the projection cannot answer
    got = _metrics.promote_eligible(vault=vault)
    assert got["total"] == 1
    assert got["by_domain"] == {"knowledge": 1}


# ── 5.2 pending age ─────────────────────────────────────────────────────────

def test_pending_age_reports_the_tail_not_just_the_count(
        atelier_env: Dict) -> None:
    """§2 point 2: gating on the count lets a workflow drain the recent items
    while the old tail rots. The metric must expose max."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "fresh", domain="operational", sensitivity="public",
                 ac_status="pending", created_at="2026-07-20T00:00:00+00:00")
    _write_claim(vault, "stale", domain="operational", sensitivity="public",
                 ac_status="pending", created_at="2026-06-15T00:00:00+00:00")
    _api.reindex(space="gorae", full=True)

    got = _metrics.pending_age(as_of=datetime.date(2026, 7, 23), vault=vault)
    assert got["count"] == 2
    assert got["max"] == 38                      # 2026-06-15 → 2026-07-23


def test_pending_age_is_reproducible_because_as_of_is_a_parameter(
        atelier_env: Dict) -> None:
    """§4.2: this is the one wall-clock-derived metric. A counter that read the
    clock itself would give a different verdict tomorrow on identical commits,
    breaking both reproducibility and any `unchanged: true` envelope over it."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "p", domain="operational", sensitivity="public",
                 ac_status="pending", created_at="2026-07-01T00:00:00+00:00")
    _api.reindex(space="gorae", full=True)

    day1 = _metrics.pending_age(as_of=datetime.date(2026, 7, 23), vault=vault)
    day2 = _metrics.pending_age(as_of=datetime.date(2026, 7, 23), vault=vault)
    later = _metrics.pending_age(as_of=datetime.date(2026, 8, 23), vault=vault)
    assert day1 == day2                          # same as_of → same verdict
    assert later["max"] > day1["max"]            # and as_of genuinely drives it


# ── 5.3 guard liveness ──────────────────────────────────────────────────────

def test_guard_liveness_counts_active_lines_not_file_existence(
        tmp_path: Path) -> None:
    """The RFC 0008 defect, made visible: a file that exists carrying only
    comments reports healthy at every enforcement point while scanning nothing.
    `pii_file_present` and `pii_active_patterns` must disagree here."""
    p = tmp_path / "pii_patterns.txt"
    p.write_text("# a comment\n\n#another\n", encoding="utf-8")
    got = _metrics.guard_liveness(pii_patterns_path=p)
    assert got["pii_file_present"] is True
    assert got["pii_active_patterns"] == 0       # the whole point


def test_guard_liveness_counts_real_patterns(tmp_path: Path) -> None:
    p = tmp_path / "pii_patterns.txt"
    p.write_text("# header\nfoo\nbar\n\n", encoding="utf-8")
    assert _metrics.guard_liveness(pii_patterns_path=p)["pii_active_patterns"] == 2


def test_guard_liveness_on_an_absent_file(tmp_path: Path) -> None:
    got = _metrics.guard_liveness(pii_patterns_path=tmp_path / "nope.txt")
    assert got == {"pii_active_patterns": 0, "pii_file_present": False}


# ── 5.5 lens surface coverage ───────────────────────────────────────────────

def test_lens_coverage_reads_the_live_handler_signature() -> None:
    """§3.2 rule 2 + §5.5: the denominator is schema data so it cannot be shrunk
    to meet a bound; the numerator is introspected so the schema file cannot
    *claim* coverage the code does not have."""
    got = _metrics.lens_surface_coverage()
    assert got["total"] == len(_metrics._declared_surfaces())
    assert got["covered"] + len(got["missing_names"]) == got["total"]
    # today exactly one surface is scoped (RFC 0009 §2); G3 moves this to 6/6
    assert got["covered_names"] == ["recall"]


def test_lens_coverage_cannot_be_claimed_by_the_schema_file(monkeypatch) -> None:
    """Declaring a surface that no handler implements must count as MISSING, not
    as covered — otherwise `6/6` is reachable by editing yaml alone."""
    monkeypatch.setattr(_metrics, "_declared_surfaces",
                        lambda: [{"name": "recall"}, {"name": "no_such_tool"}])
    got = _metrics.lens_surface_coverage()
    assert got["covered"] == 1
    assert got["missing_names"] == ["no_such_tool"]


def test_lens_coverage_follows_a_handler_gaining_lens(monkeypatch) -> None:
    """The inverse: wiring `lens` into a real handler is what moves the number,
    which is what makes the G3 bound mean something."""
    from runtime.service import tools as _tools

    async def _fake(query: str, lens: str = "dev"):        # noqa: ANN202
        return {}

    monkeypatch.setattr(_metrics, "_declared_surfaces",
                        lambda: [{"name": "search"}])
    monkeypatch.setattr(_tools, "_h_search", _fake, raising=False)
    assert _metrics.lens_surface_coverage()["covered"] == 1


# ── the block ───────────────────────────────────────────────────────────────

def test_metrics_block_omits_an_unmeasurable_metric(atelier_env: Dict) -> None:
    """§5.4: abstention is key-absence, never a zero. `cross_project_noise` has
    no fixture until G3, so its key must simply not be there — a `0.0` would
    silently PASS a `≤ 0.15` ceiling and report green on a lens returning
    nothing."""
    got = _metrics.metrics(as_of=datetime.date(2026, 7, 23),
                           vault=Path(_cl._vault_root()))
    assert "cross_project_noise" not in got
    assert set(got) == {"as_of", "promote_eligible", "pending_age",
                        "guard_liveness", "lens_surface_coverage"}


def test_metrics_land_beside_census_never_inside_it(atelier_env: Dict) -> None:
    """§3.3 + §11.6: INV-1 (`_census_kind_totals`) iterates the census's
    top-level keys as node kinds and FAILs on any decrease. A counter a goal
    must drive DOWN would become a gate against its own goal if it landed
    there."""
    from runtime.service.learnings import baseline as _baseline
    from runtime.service.learnings import verify as _verify

    out = _baseline.generate(captured_date="2026-07-23",
                             vault=Path(_cl._vault_root()))
    assert "metrics" in out and "metrics" not in out["census"]
    assert "promote_eligible" not in _verify._census_kind_totals(out["census"])
