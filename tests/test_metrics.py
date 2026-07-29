"""RFC 0009 §5 — the goal-program metrics.

The tests that matter here are the ones that would FAIL against a plausible
wrong implementation, because every counter in this module is authored by the
same change it grades (§3.2). Two in particular:

- `test_promote_eligible_matches_the_production_predicate` fails if the counter
  is re-implemented rather than wrapped — including the specific attack §3.2
  names, dropping the born-accepted branch so knowledge claims stop counting.
- `test_lens_param_cannot_be_claimed_by_the_schema_file` fails if the numerator
  is ever taken from the declaration instead of the live handler signature.

And one test exists to pin a LIMIT rather than a property:
`test_lens_param_present_does_not_prove_the_lens_is_honoured` asserts that a
handler accepting `lens` and ignoring it still counts — because that is true,
and naming the metric `lens_param_present` is the honest response. An earlier
revision called it `lens_surface_coverage` and asserted the same behaviour as
the *passing* case, which certified the attack instead of disclosing it.
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

    This is the divergence test. The attack it blocks: re-implement the counter
    instead of wrapping `claims_io.is_promote_eligible`, so the reported total
    drifts from what `promote.propose` would actually list.

    G2 (RFC 0009) narrowed eligibility to the operational lane: an atomize-born
    knowledge claim (no ac_status) is NO LONGER eligible — but the counter must
    still surface it in `by_domain` as a candidate that scored `0`, never drop
    its key (an absent domain key is a raise in the contract evaluator, not a
    satisfied zero).
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
    rows = _propose._eligible(limit=10_000)
    assert len(rows) < 10_000, "cap is binding — the comparison would be vacuous"
    assert got["total"] == len(rows)
    # born-accepted knowledge is gated OUT (0) but stays a visible domain key;
    # only the reviewed operational claim counts toward the total.
    assert got["by_domain"] == {"knowledge": 0, "operational": 1}
    # one tally: the split must always reconstruct the total (§ census discipline)
    assert sum(got["by_domain"].values()) == got["total"]


def test_promote_eligible_falls_back_on_a_cold_db(atelier_env: Dict) -> None:
    """§5.1: `projection_counts` answers None on a cold DB, and under the
    abstain rule a None would become key-absence and abort the run. The counter
    must route through the filesystem fallback instead."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "one", domain="operational", sensitivity="public",
                 ac_status="passed")
    # deliberately NOT reindexed — the projection cannot answer
    got = _metrics.promote_eligible(vault=vault)
    assert got["total"] == 1
    assert got["by_domain"] == {"operational": 1}


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
    `_file_present` and `pii_active_patterns` must disagree here."""
    p = tmp_path / "pii_patterns.txt"
    p.write_text("# a comment\n\n#another\n", encoding="utf-8")
    got = _metrics.guard_liveness(pii_patterns_path=p)
    assert got["_file_present"] is True
    assert got["pii_active_patterns"] == 0       # the whole point


def test_guard_liveness_counts_real_patterns(tmp_path: Path) -> None:
    p = tmp_path / "pii_patterns.txt"
    p.write_text("# header\nfoo\nbar\n\n", encoding="utf-8")
    assert _metrics.guard_liveness(pii_patterns_path=p)["pii_active_patterns"] == 2


def test_guard_liveness_on_an_absent_file(tmp_path: Path) -> None:
    got = _metrics.guard_liveness(pii_patterns_path=tmp_path / "nope.txt")
    assert got == {"pii_active_patterns": 0, "_file_present": False}


# ── 5.3c seeded probe — liveness by execution, not by count (G1) ────────────

def test_seeded_probe_blocked_is_1_on_the_real_hook() -> None:
    """The shipped hook, executed hermetically: seeded match blocks AND a clean
    stage passes → 1. This is what pii_active_patterns alone cannot prove."""
    import shutil
    if shutil.which("git") is None:
        import pytest
        pytest.skip("probe needs git")
    assert _metrics.seeded_probe_blocked() == 1


def test_seeded_probe_reports_0_when_the_guard_is_gutted(tmp_path: Path) -> None:
    """Reverse test: a hook that always passes must score 0 (a REAL defect
    reading, §5.4 — never an abstain), else the metric can't fail."""
    import shutil
    if shutil.which("git") is None:
        import pytest
        pytest.skip("probe needs git")
    gutted = tmp_path / "pre-commit"
    gutted.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    assert _metrics.seeded_probe_blocked(hook=gutted) == 0


def test_seeded_probe_abstains_without_a_hook(tmp_path: Path) -> None:
    """No hook script → the probe cannot measure → None (leaf omitted), the
    same abstain-not-fabricate rule every other metric follows."""
    assert _metrics.seeded_probe_blocked(hook=tmp_path / "missing") is None


def test_seeded_probe_is_hermetic_against_git_env_leakage(
        tmp_path: Path, monkeypatch) -> None:
    """Review [MUST] regression: with GIT_DIR/GIT_INDEX_FILE exported (the
    standard environment inside any git hook), the probe must NOT operate on
    the caller's repo — its scratch git must ignore inherited GIT_* entirely.
    The victim repo's index must stay untouched and the score must stay 1."""
    import shutil, subprocess, os
    if shutil.which("git") is None:
        import pytest
        pytest.skip("probe needs git")
    victim = tmp_path / "victim"
    victim.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=victim, check=True)
    monkeypatch.setenv("GIT_DIR", str(victim / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(victim / ".git" / "index"))
    assert _metrics.seeded_probe_blocked() == 1
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    staged = subprocess.run(["git", "ls-files"], cwd=victim, env=clean,
                            capture_output=True, text=True).stdout
    assert "leak.md" not in staged        # the caller's repo was never written


def test_seeded_probe_ignores_inherited_size_knob(monkeypatch) -> None:
    """Review [MUST] regression: an inherited ATELIER_MAX_STAGED_BYTES=5 made
    the clean stage trip layer 1 and the probe report a fabricated 0 against a
    healthy guard. The probe must pin the knob, not inherit it."""
    import shutil
    if shutil.which("git") is None:
        import pytest
        pytest.skip("probe needs git")
    monkeypatch.setenv("ATELIER_MAX_STAGED_BYTES", "5")
    assert _metrics.seeded_probe_blocked() == 1


def test_metrics_block_merges_probe_into_guard_liveness(atelier_env: Dict) -> None:
    """The contract-facing shape: metrics().guard_liveness carries the
    seeded_probe_blocked leaf when measurable, alongside pii_active_patterns."""
    import shutil
    if shutil.which("git") is None:
        import pytest
        pytest.skip("probe needs git")
    out = _metrics.metrics(as_of=datetime.date(2026, 7, 29),
                           vault=Path(_cl._vault_root()))
    gl = out["guard_liveness"]
    assert gl.get("seeded_probe_blocked") == 1
    assert "pii_active_patterns" in gl


# ── 5.5 lens surface coverage ───────────────────────────────────────────────

def test_lens_param_reads_the_live_handler_signature() -> None:
    """§3.2 rule 2 + §5.5: the denominator is schema data so it cannot be shrunk
    to meet a bound; the numerator is introspected so the schema file cannot
    *claim* coverage the code does not have."""
    got = _metrics.lens_param_present()
    assert got["total"] == len(_metrics._declared_surfaces())
    assert got["covered"] + len(got["_absent"]) + len(got["_unimplemented"]) \
        == got["total"]
    assert got["unimplemented"] == 0            # every declared surface exists
    # today exactly one surface takes a lens (RFC 0009 §2); G3 moves this to 6/6
    assert got["_present"] == ["recall"]


def test_lens_param_cannot_be_claimed_by_the_schema_file(monkeypatch) -> None:
    """Declaring a surface that no handler implements must count as MISSING, not
    as covered — otherwise `6/6` is reachable by editing yaml alone."""
    monkeypatch.setattr(_metrics, "_declared_surfaces",
                        lambda: [{"name": "recall"}, {"name": "no_such_tool"}])
    got = _metrics.lens_param_present()
    assert got["covered"] == 1
    # a declared-but-nonexistent handler is UNIMPLEMENTED, not lens-less — the
    # distinction is what tells a reader the yaml is wrong rather than the code
    assert got["_unimplemented"] == ["no_such_tool"]


def test_lens_param_present_does_not_prove_the_lens_is_honoured(
        monkeypatch) -> None:
    """A DISCLOSURE test, not a property test.

    A handler that accepts `lens` and discards it counts as present — because
    "accepts" is all a signature can prove. With a `covered = 6` bound, adding
    the parameter to five handlers and ignoring it would satisfy every contract
    layer while the surfaces stay unscoped. The metric is named for its limit,
    and G3 must add a behavioural gate (call the surface under two lenses,
    require the results to differ) before that bound means anything.
    """
    from runtime.service import tools as _tools

    async def _accepts_but_ignores(query: str, lens: str = "dev"):   # noqa: ANN202
        return {"rows": "everything, unscoped"}

    monkeypatch.setattr(_metrics, "_declared_surfaces",
                        lambda: [{"name": "search"}])
    monkeypatch.setattr(_tools, "_h_search", _accepts_but_ignores, raising=False)
    assert _metrics.lens_param_present()["covered"] == 1   # the known blind spot


def test_lens_param_abstains_when_the_declaration_is_unreadable(
        monkeypatch) -> None:
    """§5.4: a broken schema file omits ONE key. Raising here would abort the
    whole verification — every other metric and every global invariant with
    it — because `verify_against` calls `baseline.generate`."""
    monkeypatch.setattr(_metrics, "_SURFACES_YAML", Path("/nonexistent.yaml"))
    assert _metrics._declared_surfaces() is None
    assert _metrics.lens_param_present() is None


# ── the block ───────────────────────────────────────────────────────────────

def test_metrics_block_omits_an_unmeasurable_metric(atelier_env: Dict,
                                                    tmp_path: Path) -> None:
    """§5.4: abstention is key-absence, never a zero. With no probe fixture
    (the fresh-clone / CI state, pinned here via monkey-free probes_path), the
    `cross_project_noise` key must simply not be there — a `0.0` would
    silently PASS a `≤ 0.15` ceiling and report green on a lens returning
    nothing."""
    got = _metrics.metrics(as_of=datetime.date(2026, 7, 23),
                           vault=Path(_cl._vault_root()),
                           probes_path=tmp_path / "absent.json")
    # the always-computed metrics are present; the withheld one is not; and no
    # UNKNOWN key leaks in (subset-of-allowed keeps the leak guard the exact-set
    # assertion used to give, while tolerating conditionally-present metrics).
    assert {"promote_eligible", "pending_age", "guard_liveness"} <= set(got)
    assert set(got) <= {"promote_eligible", "pending_age", "guard_liveness",
                        "lens_param_present", "dangling_links", "dangling_new",
                        "cross_project_noise"}
    assert "cross_project_noise" not in got          # absent fixture → omitted
    # capture metadata is NOT a metric leaf: §3.4 default-deny would trip on a
    # value that changes every run, and §3.5 allows no non-numeric waiver.
    assert "as_of" not in got


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


def test_pending_age_abstains_when_the_tail_is_unmeasurable(
        atelier_env: Dict) -> None:
    """§5.4 applied to the metric that shipped without it.

    Undated pending claims were dropped from the age list but still counted, so
    a queue of 36 unparseable claims reported `max: 0` — which PASSES a `≤ 7`
    ceiling while the backlog rots, and lets `count` rise unchallenged. The keys
    must be absent so a contract naming them raises instead.
    """
    vault = Path(_cl._vault_root())
    import uuid
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    for n in ("u1", "u2"):
        eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claim-{n}"))
        (d / f"{n}.md").write_text(                     # no created_at at all
            f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
            f"domain: operational\nsensitivity: public\nac_status: pending\n"
            f"statement: statement of {n}\n---\n\nbody\n", encoding="utf-8")
    _api.reindex(space="gorae", full=True)

    got = _metrics.pending_age(as_of=datetime.date(2026, 7, 23), vault=vault)
    assert got["count"] == 2 and got["dated"] == 0
    assert "max" not in got and "p50" not in got       # abstain, never zero


def test_pending_age_clamps_a_claim_newer_than_as_of(atelier_env: Dict) -> None:
    """Verifying against a stale program anchor puts `as_of` BEFORE claims that
    exist, and a max over mixed-sign values is not a tail measurement."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "future", domain="operational", sensitivity="public",
                 ac_status="pending", created_at="2026-08-01T00:00:00+00:00")
    _api.reindex(space="gorae", full=True)
    got = _metrics.pending_age(as_of=datetime.date(2026, 7, 23), vault=vault)
    assert got["max"] == 0                             # not negative


def test_promote_eligible_parity_between_projection_and_filesystem(
        atelier_env: Dict) -> None:
    """One tally, two sources — `census.py`'s discipline. An earlier revision
    read `total` from one query and `by_domain` from a second, so a DB hiccup
    between them produced `total: N` with an empty split."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "k1", domain="knowledge", sensitivity="public")
    _write_claim(vault, "o1", domain="operational", sensitivity="public",
                 ac_status="passed")
    from_fs = _metrics.promote_eligible(vault=vault)   # cold DB → filesystem
    _api.reindex(space="gorae", full=True)
    from_db = _metrics.promote_eligible(vault=vault)   # warm DB → projection
    assert from_fs == from_db
    assert sum(from_db["by_domain"].values()) == from_db["total"]


def test_metrics_survive_into_a_written_baseline(atelier_env: Dict,
                                                 tmp_path: Path) -> None:
    """`generate` is not the shipping path — `write` is, and it round-trips
    through JSON."""
    import json
    from runtime.service.learnings import baseline as _baseline
    out = tmp_path / "b.json"
    _baseline.write(out, vault=Path(_cl._vault_root()),
                    captured_date="2026-07-23", about="RFC 0009 test anchor")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["_about"] == "RFC 0009 test anchor"
    assert data["as_of"] == "2026-07-23"               # top level, not in metrics
    assert "promote_eligible" in data["metrics"]


def test_verify_still_passes_against_a_metrics_less_baseline(
        atelier_env: Dict, tmp_path: Path) -> None:
    """The frozen record stays frozen. `0006-baseline.json` predates the metrics
    block; adding one must not make the committed anchor unverifiable."""
    import json
    from runtime.service.learnings import baseline as _baseline
    from runtime.service.learnings import verify as _verify
    before = _baseline.generate(vault=Path(_cl._vault_root()),
                                captured_date="2026-07-23")
    before.pop("metrics")                              # a pre-0009 anchor
    old = tmp_path / "old.json"
    old.write_text(json.dumps(before), encoding="utf-8")

    report = _verify.verify_against(old, "P0", vault=Path(_cl._vault_root()),
                                    require_committed=False)
    assert report["passed"] is True


# ── the two fixes a revert would not have noticed ────────────────────────────
#
# Review reverted both of these and the whole 749-test suite stayed green. That
# is the definition of an unprotected fix, and the vault one is load-bearing:
# RFC 0009 §8.1 names it as the prerequisite for the FAILING side of the G0
# two-sided gate. The conftest points `_vault_root()` at the temp vault, so the
# bug is invisible to every ordinary test by construction — proving it needs two
# DIFFERENT vaults.


def test_surfacing_measures_the_vault_it_is_given_not_the_configured_one(
        atelier_env: Dict, tmp_path: Path) -> None:
    """Without the `vault` parameter, `baseline.generate(vault=B)` measured
    `census`/`metrics` over B while `surfacing`/`eval.self_probe` silently read
    the configured root A — so an injected delta could go unobserved and the run
    would report PASS on a change it never saw."""
    from runtime.service.learnings import surfacing as _surfacing
    configured = Path(_cl._vault_root())
    _write_claim(configured, "here", domain="operational", sensitivity="public",
                 ac_status="passed")
    _api.reindex(space="gorae", full=True)

    empty = tmp_path / "other-vault"
    empty.mkdir()
    assert _surfacing.audit(vault=configured)["total"] > 0
    assert _surfacing.audit(vault=empty)["total"] == 0      # genuinely scoped


def test_eval_self_probe_follows_the_same_vault_as_surfacing(
        atelier_env: Dict, tmp_path: Path) -> None:
    """`eval._self_probe_block` resolved the root internally, so the two blocks
    that share an omission definition could measure different vaults."""
    from runtime.service.learnings import eval as _eval
    configured = Path(_cl._vault_root())
    _write_claim(configured, "probe-me", domain="operational",
                 sensitivity="public", ac_status="passed")
    _api.reindex(space="gorae", full=True)

    empty = tmp_path / "other-vault"
    empty.mkdir()
    assert _eval.run(vault=empty)["self_probe"]["probes"] == 0
    assert _eval.run(vault=configured)["self_probe"]["probes"] > 0


def test_baseline_cli_passes_about_through() -> None:
    """`--about` existed on `generate`/`write` but not on the command documented
    for capturing an anchor, so a 0009 baseline would have been stamped with the
    0006 description — the misdescription the parameter exists to prevent."""
    from runtime import cli as _cli
    args = _cli.build_parser().parse_args(
        ["baseline", "--out", "/tmp/x.json", "--about", "RFC 0009 anchor"])
    assert args.about == "RFC 0009 anchor"


def test_guard_liveness_abstains_on_an_unreadable_file(tmp_path: Path) -> None:
    """§5.4 + the SHOULD 7 class: this file is per-machine and user-managed, so
    a latin-1 regex must not abort every other metric and every invariant."""
    p = tmp_path / "pii_patterns.txt"
    p.write_bytes(b"caf\xe9\n")                    # invalid UTF-8
    got = _metrics.guard_liveness(pii_patterns_path=p)
    assert "pii_active_patterns" not in got        # abstain, not a zero
    assert got["_unreadable"] is True


def test_lens_param_separates_a_yaml_typo_from_a_missing_lens(
        monkeypatch) -> None:
    """A declared surface with no handler caps `covered` permanently; folding it
    into `_absent` leaves nothing in the output to say why."""
    monkeypatch.setattr(_metrics, "_declared_surfaces",
                        lambda: [{"name": "recall"}, {"name": "search"},
                                 {"name": "typo_here"}])
    got = _metrics.lens_param_present()
    assert got["covered"] == 1 and got["total"] == 3
    assert got["_absent"] == ["search"]
    assert got["unimplemented"] == 1 and got["_unimplemented"] == ["typo_here"]


def test_baseline_tolerates_a_malformed_captured_date(atelier_env: Dict) -> None:
    """`verify_against` feeds `captured_date` straight from an on-disk anchor."""
    from runtime.service.learnings import baseline as _baseline
    out = _baseline.generate(vault=Path(_cl._vault_root()), captured_date="not-a-date")
    assert "metrics" in out                        # did not raise


# ── dangling links (enables G5 wiki-link repair) ─────────────────────────────

def test_dangling_links_counts_the_broken_links_view(atelier_env: Dict) -> None:
    """§3.2 rule 1: the counter must equal the production referential-integrity
    definition (the `broken_links` view), so a repair goal cannot be gamed by a
    counter that measures something other than what it fixes."""
    from runtime.util import db as _db
    vault = Path(_cl._vault_root())
    # one page linking to a real target and to a missing one
    from tests.conftest import write_page
    write_page(vault / "wiki" / "entities" / "real.md",
               {"title": "Real", "type": "entity", "category": "concept",
                "created": "2026-05-27", "updated": "2026-05-27"}, "# Real\n")
    write_page(vault / "wiki" / "entities" / "src.md",
               {"title": "Src", "type": "entity", "category": "concept",
                "created": "2026-05-27", "updated": "2026-05-27"},
               "# Src\n\nlinks to [[real]] and to [[does-not-exist]].\n")
    _api.reindex(space="gorae", full=True)

    got = _metrics.dangling_links()
    conn = _db.connect()
    view_count = conn.execute("SELECT COUNT(*) FROM broken_links").fetchone()[0]
    conn.close()
    assert got["total"] == view_count == 1            # only [[does-not-exist]]
    # by_type is SEEDED with every known link_type at 0 (namespace stability),
    # with the one real broken wikilink counted.
    assert got["by_type"] == {"wikilink": 1, "gorae": 0, "workshop": 0, "concept": 0}


def test_dangling_links_seeded_keys_are_stable_across_baselines(
        atelier_env: Dict) -> None:
    """§3.4: an unseeded by_type key that appeared only when a type had a broken
    edge would trip the ENVELOPE union rule on an unrelated goal. All four known
    types are always present, so the keyset never shifts between baselines."""
    from tests.conftest import write_page
    write_page(Path(_cl._vault_root()) / "wiki" / "entities" / "p.md",
               {"title": "P", "type": "entity", "category": "concept",
                "created": "2026-05-27", "updated": "2026-05-27"}, "# P\n")
    _api.reindex(space="gorae", full=True)
    got = _metrics.dangling_links()
    assert set(got["by_type"]) == {"wikilink", "gorae", "workshop", "concept"}


def test_dangling_links_real_zero_on_a_healthy_reindexed_vault(
        atelier_env: Dict) -> None:
    """The distinction the cold-DB abstain rests on: a vault with pages and no
    broken links is a REAL 0 (a floor/eq bound behaves), NOT an abstention."""
    from tests.conftest import write_page
    vault = Path(_cl._vault_root())
    write_page(vault / "wiki" / "entities" / "solo.md",
               {"title": "Solo", "type": "entity", "category": "concept",
                "created": "2026-05-27", "updated": "2026-05-27"},
               "# Solo\n\nno links here.\n")
    _api.reindex(space="gorae", full=True)
    got = _metrics.dangling_links()
    assert got is not None and got["total"] == 0      # real measurement, not None


def test_dangling_links_abstains_on_an_un_reindexed_projection(
        atelier_env: Dict) -> None:
    """The load-bearing abstain: `db.connect()` CREATEs the DB (IF NOT EXISTS),
    so a cold DB does NOT raise — it returns an empty `broken_links`, i.e. a
    fabricated 0. The guard keys on an EMPTY projection (no pages), not a connect
    error, so a never-reindexed vault abstains (None → key omitted) rather than
    reporting a green 0 a `{eq: 0}` repair bound would pass vacuously."""
    # atelier_env points DB_PATH at a fresh temp cache and does NOT reindex, so
    # the projection is genuinely empty here.
    from runtime.util import db as _db
    conn = _db.connect()
    assert conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0  # cold
    conn.close()
    assert _metrics.dangling_links() is None
    out = _metrics.metrics(as_of=datetime.date(2026, 7, 27),
                           vault=Path(_cl._vault_root()))
    assert "dangling_links" not in out                # omitted, not a zero


def test_dangling_links_abstains_on_an_unreadable_db(atelier_env: Dict,
                                                     monkeypatch) -> None:
    """The other abstain path: a genuinely unreadable DB (connect raises)."""
    from runtime.util import db as _db
    monkeypatch.setattr(_db, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _metrics.dangling_links() is None


def test_dangling_links_in_the_metrics_block_when_measurable(
        atelier_env: Dict) -> None:
    from tests.conftest import write_page
    write_page(Path(_cl._vault_root()) / "wiki" / "entities" / "p.md",
               {"title": "P", "type": "entity", "category": "concept",
                "created": "2026-05-27", "updated": "2026-05-27"}, "# P\n")
    _api.reindex(space="gorae", full=True)
    out = _metrics.metrics(as_of=datetime.date(2026, 7, 27),
                           vault=Path(_cl._vault_root()))
    assert "dangling_links" in out and "total" in out["dangling_links"]


# ── dangling_new — the accepted-baseline regression signal ───────────────────

def _write_dangling_baseline(path: Path, accepted: Dict[str, list]) -> None:
    """Author a baseline of {category: [targets]} at `path`."""
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": 1,
           "categories": {c: {"targets": t} for c, t in accepted.items()}}
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")


def _seed_two_danglers(vault: Path) -> None:
    """A page whose body breaks two wikilinks: [[does-not-exist]] and [[also-missing]]."""
    from tests.conftest import write_page
    write_page(vault / "wiki" / "entities" / "src.md",
               {"title": "Src", "type": "entity", "category": "concept",
                "created": "2026-05-27", "updated": "2026-05-27"},
               "# Src\n\nlinks to [[does-not-exist]] and [[also-missing]].\n")
    _api.reindex(space="gorae", full=True)


def test_dangling_new_is_set_difference_not_count(atelier_env: Dict, tmp_path) -> None:
    """`new` = current dangling TARGETS minus accepted — so accepting one of two
    leaves exactly the other, by identity, not by count."""
    _seed_two_danglers(Path(_cl._vault_root()))
    baseline = tmp_path / "dangling-baseline.yaml"
    _write_dangling_baseline(baseline, {"boundary-external": ["does-not-exist"]})
    got = _metrics.dangling_new(baseline_path=baseline)
    assert got is not None
    assert got["new"] == 1
    assert got["_new_targets"] == ["also-missing"]     # by identity, not just count
    assert got["_accepted"] == 1


def test_dangling_new_zero_when_all_current_are_accepted(
        atelier_env: Dict, tmp_path) -> None:
    """The closed-arc state: every current dangler is in the baseline → new = 0,
    even though the raw dangling count is non-zero."""
    _seed_two_danglers(Path(_cl._vault_root()))
    baseline = tmp_path / "dangling-baseline.yaml"
    _write_dangling_baseline(
        baseline, {"boundary-external": ["does-not-exist", "also-missing"]})
    got = _metrics.dangling_new(baseline_path=baseline)
    assert got is not None and got["new"] == 0 and got["_new_targets"] == []
    # raw dangling_links still reports the residual — the two signals are distinct
    assert _metrics.dangling_links()["by_type"]["wikilink"] == 2


def test_dangling_new_abstains_without_a_baseline(atelier_env: Dict, tmp_path) -> None:
    """No baseline → every target would read as 'new'; that fabricated alarm is
    the §5.4 abstain case, so it returns None (key omitted) rather than alarm."""
    _seed_two_danglers(Path(_cl._vault_root()))
    missing = tmp_path / "nope.yaml"
    assert _metrics.dangling_new(baseline_path=missing) is None


def test_dangling_new_abstains_on_a_present_but_shapeless_baseline(
        atelier_env: Dict, tmp_path) -> None:
    """A file that exists but has no recoverable `categories` mapping (a
    truncated/empty write, a renamed key, `categories: null`, a top-level list)
    must ABSTAIN — an empty accepted-set would read every current dangler as a
    regression, the same fabricated alarm as a missing file. Only an EXPLICIT
    `categories: {…}` mapping declares the accepted set."""
    _seed_two_danglers(Path(_cl._vault_root()))
    for body in ("", "   \n", "- just\n- a list\n", "schema_version: 1\n",
                 "categories: null\n"):
        p = tmp_path / "b.yaml"
        p.write_text(body, encoding="utf-8")
        assert _metrics.dangling_new(baseline_path=p) is None, repr(body)
    # an EXPLICIT empty categories mapping is a real (empty) accepted-set, not an
    # abstain: the author is saying "nothing is accepted yet" → all read as new.
    p = tmp_path / "explicit-empty.yaml"
    p.write_text("categories: {}\n", encoding="utf-8")
    got = _metrics.dangling_new(baseline_path=p)
    assert got is not None and got["new"] == got["_current_distinct"] > 0


def test_dangling_new_abstains_on_an_un_reindexed_projection(
        atelier_env: Dict, tmp_path) -> None:
    """Mirrors dangling_links: an empty projection abstains rather than reporting
    a fabricated 0-new against a DB that was never built."""
    from runtime.util import db as _db
    baseline = tmp_path / "dangling-baseline.yaml"
    _write_dangling_baseline(baseline, {"boundary-external": ["x"]})
    conn = _db.connect()
    assert conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0   # cold
    conn.close()
    assert _metrics.dangling_new(baseline_path=baseline) is None


def test_dangling_new_in_metrics_block_reads_the_vault_baseline(
        atelier_env: Dict) -> None:
    """End-to-end through the default path: a baseline authored at the vault's
    graph/meta/ is picked up by metrics(); `new` is the one numeric (bound-able)
    leaf, the rest are `_`-prefixed diagnostics (§5.1.1)."""
    vault = Path(_cl._vault_root())
    _seed_two_danglers(vault)
    _write_dangling_baseline(
        vault / "graph" / "meta" / "dangling-baseline.yaml",
        {"boundary-external": ["does-not-exist", "also-missing"]})
    out = _metrics.metrics(as_of=datetime.date(2026, 7, 27), vault=vault)
    assert "dangling_new" in out
    dnew = out["dangling_new"]
    assert dnew["new"] == 0
    numeric_bare = [k for k, v in dnew.items()
                    if not k.startswith("_") and isinstance(v, (int, float))
                    and not isinstance(v, bool)]
    assert numeric_bare == ["new"]                     # only `new` is envelope-bound


def test_doctor_d8_warns_on_a_new_dangler_then_ok_once_accepted(
        atelier_env: Dict) -> None:
    """D8 surfaces a broken wikilink absent from the accepted baseline as WARN,
    and returns to OK once the baseline accepts it — the ratchet in the human
    health readout."""
    from runtime.doctor import diagnostics
    from runtime.util import config
    vault = Path(_cl._vault_root())
    _seed_two_danglers(vault)                          # [[does-not-exist]], [[also-missing]]
    baseline = vault / "graph" / "meta" / "dangling-baseline.yaml"
    _write_dangling_baseline(baseline, {"boundary-external": ["does-not-exist"]})
    cfg = config.load()
    d8 = next(d for d in diagnostics.run_all(cfg) if d.id == "D8")
    assert d8.severity == "WARN" and d8.details["new"] == 1
    assert d8.details["sample"] == ["also-missing"]
    # accept the second one → the ratchet closes, D8 goes OK
    _write_dangling_baseline(
        baseline, {"boundary-external": ["does-not-exist", "also-missing"]})
    d8b = next(d for d in diagnostics.run_all(cfg) if d.id == "D8")
    assert d8b.severity == "OK"


def test_doctor_d8_ok_without_a_baseline(atelier_env: Dict) -> None:
    """No baseline → D8 cannot assert a regression, so it abstains to OK rather
    than flag every boundary reference (mirrors the metric's abstain)."""
    from runtime.doctor import diagnostics
    from runtime.util import config
    _seed_two_danglers(Path(_cl._vault_root()))        # danglers present, no baseline authored
    d8 = next(d for d in diagnostics.run_all(config.load()) if d.id == "D8")
    assert d8.severity == "OK"
