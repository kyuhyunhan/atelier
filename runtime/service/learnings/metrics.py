"""RFC 0009 §5 — the goal-program metrics: quantities a *deliberate reduction*
can be gated on.

These are NOT part of the census. The census answers "how is the graph
composed?" and INV-1 (`verify._census_kind_totals`) reads it as a monotone
"no node kind shrank" gate. A counter that a goal must drive DOWN — promote
eligibility from 830 to ~23 — would, sitting inside `census`, silently become a
no-shrink gate on the exact quantity the goal exists to reduce (RFC 0009 §3.3).
So they live in a sibling `metrics` block, invisible to INV-1 by construction,
and are gated instead by the contract's INTENT/ENVELOPE clauses.

Two disciplines carry most of the weight here:

**Thin wrappers, never re-implementations** (§3.2 rule 1). The counters ship in
the same change they score, so a builder who cannot move a number could redefine
it — implementing `promote_eligible` as `ac_status == 'passed'` alone drops the
born-accepted branch, reports 23, and passes every clause on a vault that still
proposes 830 claims. Each counter therefore calls the production predicate, and
`tests/test_metrics.py` asserts equality with the path the real feature uses.

**Abstention is key-absence, never a zero** (§5.4). A metric that cannot be
measured omits its key, so the contract evaluator raises on a clause naming it.
Emitting `0.0` instead would silently *pass* a ceiling bound — reporting green
for a lens that returned nothing at all, which is the vacuous-PASS this whole
program exists to prevent.
"""
from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

_SURFACES_YAML = (Path(__file__).resolve().parents[3]
                  / "schema" / "data" / "lens_surfaces.yaml")

# Overridable for tests; production reads the operator's real guard file.
_PII_PATTERNS_PATH = Path.home() / ".atelier" / "pii_patterns.txt"


# ── 5.1 promote eligibility ─────────────────────────────────────────────────

def _tally_eligible(fms: Any) -> dict[str, Any]:
    """The ONE place an eligible claim becomes counts, shared by both data
    sources — `census.py`'s discipline, for the same reason: two paths that
    tally separately can disagree, and `sum(by_domain) != total` must not be
    representable.

    An earlier revision took `total` from one projection query and `by_domain`
    from a second, so a DB hiccup between them yielded `total: N` with an empty
    split.

    `by_domain` is keyed by every promotion CANDIDATE's domain (query-tier +
    public — `is_promote_candidate`), each counting how many of that domain's
    candidates the eligibility gate actually passes. Seeding the candidate
    domains at 0 keeps a domain that is gated fully out (G2: `knowledge`, now 0
    eligible) PRESENT as `0` rather than dropping its key: the contract scores
    `by_domain.<domain>` by an exact/delta bound, and an absent key is a hard
    raise in `contract._eval_intent`, not a satisfied zero. `total` stays
    `sum(by_domain.values())`, unaffected by the zero seeds.
    """
    from . import claims_io as _claims
    by_domain: dict[str, int] = {}
    for fm in fms:
        if not _claims.is_promote_candidate(fm):
            continue
        d = str(fm.get("domain") or "(absent)")
        by_domain.setdefault(d, 0)
        if _claims.is_promote_eligible(fm):
            by_domain[d] += 1
    return {"total": sum(by_domain.values()), "by_domain": by_domain}


def promote_eligible(*, vault: Path | None = None) -> dict[str, Any]:
    """`{total, by_domain}` over `claims_io.is_promote_eligible` — the same
    predicate `promote.propose._eligible` uses, never a re-implementation
    (RFC 0009 §3.2 rule 1).

    Projection-first with the SAME filesystem fallback the feature uses. That
    fallback is not optional: `projection_counts` answers `None` on a cold DB,
    and under the abstain rule a `None` would become key-absence and abort the
    run (§5.1).

    Note this counts the UNCAPPED pool, while `propose_all()` only ever proposes
    `_eligible()`'s first 50. That is deliberate — the G2 contract is about how
    much is eligible, not how much one proposal happens to list — but the two
    numbers are not meant to reconcile.
    """
    from . import claims_io as _claims
    from . import projection_counts as _pc

    nodes = _pc._load_nodes()
    if nodes is not None:
        return _tally_eligible(nodes["claims"])

    fms = []
    for p in _claims.iter_claim_files(vault):
        got = _claims.read_claim(p)
        if got is not None:
            fms.append(got[0])
    return _tally_eligible(fms)


# ── 5.2 pending age ─────────────────────────────────────────────────────────

def _as_date(raw: Any) -> date | None:
    s = str(raw or "")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def pending_age(*, as_of: date, vault: Path | None = None) -> dict[str, Any]:
    """Age distribution of `ac_status: pending` claims, in days.

    The count is the wrong gate: draining the recent items while a 38-day tail
    rots would satisfy it (RFC 0009 §2, point 2). `as_of` is a REQUIRED
    parameter, not `today` — this is the one wall-clock-derived metric, and a
    verifier re-run a day later must reach the same verdict on the same commits
    (§4.2), which is impossible if the counter reads the clock itself.
    """
    from . import claims_io as _claims
    from . import projection_counts as _pc

    # The ONE queue predicate (RFC 0009 G4): shared with the review surface so
    # `pending_age.count/.max` can be ASSERTED equal to what review_pending
    # serves (on a reindexed vault — the predicate is shared, the store is
    # not: this prefers the projection, the surface reads files). Before G4
    # this counted any-domain pendings while the surface filtered to
    # operational — equal on the live vault only by coincidence. NOTE the
    # frozen program anchor (docs/rfc/0009-baseline.json) captured its
    # pending_age under the old any-domain definition; values coincide (all
    # live pendings are operational) but the semantics changed here.
    fms: list[dict[str, Any]]
    nodes = _pc._load_nodes()
    if nodes is not None:
        fms = [fm for fm in nodes["claims"] if _claims.is_pending_review(fm)]
    else:
        fms = []
        for p in _claims.iter_claim_files(vault):
            got = _claims.read_claim(p)
            if got is None:
                continue
            fm, _ = got
            if _claims.is_pending_review(fm):
                fms.append(fm)

    ages: list[int] = []
    for fm in fms:
        d = _as_date(fm.get("created_at") or fm.get("created"))
        if d is not None:
            # A claim created after `as_of` is age 0, not negative. Verifying
            # against a stale program anchor otherwise takes a max over
            # mixed-sign values, which is not a tail measurement at all.
            ages.append(max(0, (as_of - d).days))
    ages.sort()

    out: dict[str, Any] = {"count": len(fms), "dated": len(ages)}
    if len(ages) < len(fms):
        # ABSTAIN — §5.4: key-absence, never a zero. An unmeasurable tail
        # returning `max: 0` would PASS a `≤ 7` ceiling while 36 undated claims
        # rot, and would even let `count` rise. That is precisely the defect
        # `cross_project_noise` is withheld to avoid; the same rule applies here.
        return out
    out["p50"] = ages[len(ages) // 2] if ages else 0
    out["max"] = ages[-1] if ages else 0
    return out


# ── dangling links (enables G5 wiki-link repair) ────────────────────────────

# The link_type enum, from `linker.py` (wikilink/vault/workshop) plus the
# `concept` edge minted in `reindex.py`. KEEP IN SYNC with those two: a new
# link_type added there but not here loses its zero-seed and reintroduces the
# keyset-instability this list fixes (an unknown type is still COUNTED correctly
# — the loop below adds it — just not pre-seeded). `by_type` is SEEDED with all so a
# key never appears or disappears between baselines — an unseeded key that shows
# up only when that type currently has a broken edge would trip the ENVELOPE's
# union rule (§3.4) on an unrelated goal, the same reason `_tally_eligible` seeds
# its domains. `wikilink` is the subset a wiki-link repair (G5) targets; the
# others are counted for honesty but are not "wiki links".
_LINK_TYPES = ("wikilink", "vault", "workshop", "concept")


def dangling_links() -> dict[str, Any] | None:
    """Count of BROKEN links — an edge whose target page does not resolve. Wraps
    the production `broken_links` view (`links.to_page_id IS NULL`), the same
    referential-integrity definition doctor and `atelier_links` use, so the
    counter cannot drift from what a repair actually has to fix (§3.2 rule 1).

    `total` is every broken edge; `by_type.wikilink` is the wiki subset a G5
    repair drives to zero (a broken `concept` edge is an idea with no page, not a
    wiki link, so a repair goal binds `by_type.wikilink`, not `total`).

    Projection-only, and it ABSTAINS by returning None (→ the `metrics()` block
    omits the key) on a cold or **un-reindexed** DB. There is no filesystem
    fallback: link resolution is a reindex operation, not a per-file fact. The
    abstain is keyed on an EMPTY projection, not a connect error — `db.connect()`
    creates the DB with `CREATE ... IF NOT EXISTS`, so a cold DB does NOT raise;
    it returns an empty `broken_links`, i.e. a fabricated `0` that would let a
    `{eq: 0}` repair bound pass vacuously against a projection that was never
    built. So: no pages → abstain; pages present with zero broken → a real `0`
    (§5.4, and the `_load_nodes` empty-projection guard it mirrors).
    """
    from ...util import db as _db
    try:
        conn = _db.connect()
        try:
            pages = _db.fetchall(conn, "SELECT COUNT(*) AS n FROM pages")[0]["n"]
            if not pages:
                return None                  # un-reindexed projection → abstain
            rows = _db.fetchall(
                conn, "SELECT link_type, COUNT(*) AS n FROM links "
                      "WHERE to_page_id IS NULL GROUP BY link_type")
        finally:
            conn.close()
    except Exception:
        return None                          # unreadable DB → abstain
    by_type = {t: 0 for t in _LINK_TYPES}
    for r in rows:
        by_type[str(r["link_type"])] = int(r["n"])   # a new type would surface too
    return {"total": sum(by_type.values()), "by_type": by_type}


# ── 5.3b new (unaccepted) dangling wikilinks — the regression signal ─────────

_DANGLING_BASELINE_REL = ("graph", "meta", "dangling-baseline.yaml")


def _default_dangling_baseline_path() -> Path | None:
    """The vault's accepted-dangling baseline path, or None if config is
    unreadable. Vault-held (not the engine repo) because some accepted targets
    are personal titles — hard rule #1."""
    try:
        from ...util import config as _config
        root = _config.vault_root()   # the ONE accessor (RFC 0001 §6 / #98)
        return root.joinpath(*_DANGLING_BASELINE_REL)
    except Exception:
        return None


def _accepted_dangling_targets(path: Path) -> set | None:
    """The accepted target strings from the baseline's `categories.*.targets`,
    or None if the file is absent/unreadable (→ `dangling_new` abstains rather
    than treat every target as a regression)."""
    if not path.is_file():
        return None
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    cats = doc.get("categories") if isinstance(doc, dict) else None
    if not isinstance(cats, dict):
        # Present but shapeless (empty/truncated file, `categories` absent or
        # null, top-level list) — abstain like the malformed-YAML path, NOT an
        # empty accepted-set. An empty set would read every current dangler as a
        # regression: the fabricated alarm §5.4 forbids. Only an EXPLICIT
        # `categories: {…}` mapping (possibly `{}`) declares the accepted set.
        return None
    accepted: set = set()
    for entry in cats.values():
        targets = entry.get("targets") if isinstance(entry, dict) else None
        if isinstance(targets, list):
            accepted.update(t for t in targets if isinstance(t, str))
    return accepted


def dangling_new(*, baseline_path: Path | None = None) -> dict[str, Any] | None:
    """NEW (unaccepted) broken wikilinks — the regression signal that stays quiet
    on the accepted residual. `new` = current dangling wikilink TARGETS minus the
    accepted set in the vault baseline (`graph/meta/dangling-baseline.yaml`), a
    SET-DIFFERENCE on target strings — never a count subtraction, so a fresh break
    plus a coincidental fix of an accepted one cannot cancel out.

    The RFC 0009 dangling arc closed with a documented residual: references outside
    the vault boundary, plus entries transferred to the RFC 0008 track (the
    index_regen track since resolved by retiring the catalog — G7). Driving the
    raw count to zero would force fabricating pages or deleting
    valid cross-references — the exact ENVELOPE violation a `{eq: 0}` goal forbids.
    So the HEADLINE reads this — a link that broke AFTER the residual was accepted
    — while the accepted residual stays silent (the lint-baseline/ratchet pattern).

    Abstains (None) when it cannot make the judgment: an empty/un-reindexed
    projection (mirrors `dangling_links`), or an absent/unreadable baseline (every
    target would read as 'new' — a fabricated alarm §5.4 forbids). `new` is the one
    numeric (bound-able) leaf; the `_`-prefixed leaves are diagnostic (§5.1.1).
    """
    path = baseline_path if baseline_path is not None else _default_dangling_baseline_path()
    if path is None:
        return None
    accepted = _accepted_dangling_targets(path)
    if accepted is None:
        return None                              # no baseline → abstain, never alarm
    from ...util import db as _db
    try:
        conn = _db.connect()
        try:
            pages = _db.fetchall(conn, "SELECT COUNT(*) AS n FROM pages")[0]["n"]
            if not pages:
                return None                      # un-reindexed → abstain (mirror dangling_links)
            rows = _db.fetchall(
                conn, "SELECT DISTINCT to_target FROM links "
                      "WHERE to_page_id IS NULL AND link_type='wikilink'")
        finally:
            conn.close()
    except Exception:
        return None
    current = {str(r["to_target"]) for r in rows}
    new = sorted(current - accepted)
    return {
        "new": len(new),
        "_new_targets": new,
        "_accepted": len(accepted),
        "_current_distinct": len(current),
    }


# ── 5.4 cross-project noise (G3) ─────────────────────────────────────────────

_PROBES_PATH = Path.home() / ".atelier" / "fixtures" / "project_probes.json"
_NOISE_MIN_YIELD = 20      # §5.4: below this, foreign_ratio is OMITTED (FAIL)
_NOISE_PER_PROBE_TOPK = 25


def cross_project_noise(*, fixture_path: Path | None = None
                        ) -> dict[str, Any] | None:
    """§5.4 — dev-session recall noise along the PROJECT axis.

    Runs the PRODUCTION dev-recall path (`recall_v7.rank_claims`, tier
    `proactive`, lens `dev`) once per fixture probe for the fixture's project,
    dedups by entry_id, and reports the fraction of returned claims whose
    `project` is some OTHER project (§3.2 rule 1: the counter is the ranking
    path the session actually uses). A claim with no project (e.g. knowledge)
    is not "some other project's work" and does not count as foreign; it does
    count toward `returned` — and because that dilutes the ratio, the
    absolute composition (`own`/`foreign`/`unowned`) is reported alongside so
    a contract can pin it (a displacement/dilution pass on the ratio alone
    leaves own-project context exactly as unserved as before).

    Two DISCLOSED divergences from what a live session sees: the metric
    measures the union of top-25 per probe (to clear the §5.4 yield floor)
    while a session is served top-5 — so a bound here reads "noise in the
    retrieval pool", not "≤15% of what a session displays"; and the legacy
    `recall.recall` merge (`_h_recall`'s migration-era side channel) is not
    measured — this counter tracks the v7 claim path a lens change actually
    scopes.

    The three §5.4 signals, distinguished:
    - fixture absent / unreadable / shapeless → **None** (metric omitted —
      environmental; a fresh clone's CI neither fails nor reports green). Only
      an explicit, well-formed fixture measures — a truncated write must not
      fabricate a verdict (the dangling-baseline rule).
    - `returned < 20` → `returned` present, `foreign_ratio` OMITTED — the
      change under-delivered; fixer-addressable FAIL, never a raise, and never
      a `0.0` that would pass the `≤ 0.15` bound on a lens returning nothing.
    - at yield → `foreign_ratio` present, a real measurement.

    The fixture lives OUT OF TREE (`~/.atelier/fixtures/project_probes.json`,
    §5.6 — real project names are hard-rule-#1 material) and is pinned by
    content in a goal contract's `pins.fixture_sha256`.
    """
    import json as _json
    path = fixture_path if fixture_path is not None else _PROBES_PATH
    if not path.is_file():
        return None
    try:
        doc = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    project = doc.get("project")
    probes = doc.get("probes")
    if not isinstance(project, str) or not project.strip():
        return None
    if not isinstance(probes, list):
        return None
    queries = [q for q in probes if isinstance(q, str) and q.strip()]
    if not queries:
        return None

    from . import recall_v7 as _rv
    seen: dict[str, dict[str, Any]] = {}
    try:
        for q in queries:
            for h in _rv.rank_claims(q, project, tier="proactive",
                                     top_k=_NOISE_PER_PROBE_TOPK, lens="dev"):
                fm = h.get("fm") or {}
                key = str(fm.get("entry_id") or h.get("slug"))
                seen[key] = h
    except Exception:
        return None                    # recall path broke → environmental abstain
    returned = len(seen)
    out: dict[str, Any] = {"project": project, "returned": returned}
    if returned >= _NOISE_MIN_YIELD:
        own = foreign = unowned = 0
        for h in seen.values():
            p = str((h.get("fm") or {}).get("project") or "")
            if not p:
                unowned += 1               # e.g. knowledge — nobody's project
            elif p == project:
                own += 1
            else:
                foreign += 1
        # The ratio alone is dilutable (review MUST, PR #93): a ranking change
        # that floods recall with UNOWNED claims displaces foreign ones from
        # the per-probe top-k and drops the ratio under the bound while not
        # one additional own-project claim is served. The absolute leaves let
        # a contract pin the composition (e.g. bind `foreign` down AND `own`
        # up), closing the displacement pass. Conditional on yield, same as
        # the ratio — the subtree is new, so the keyset stays stable.
        out["own"] = own
        out["foreign"] = foreign
        out["unowned"] = unowned
        out["foreign_ratio"] = round(foreign / returned, 4)
    return out


# ── 5.3 guard liveness ──────────────────────────────────────────────────────

def guard_liveness(*, pii_patterns_path: Path | None = None) -> dict[str, Any]:
    """How many guard patterns are ACTIVE — not whether a file exists.

    RFC 0008 §6 specified the absent-file case deliberately (a no-op pass). The
    case it left unspecified is a file that exists carrying only comments: both
    absorb enforcement points and `scripts/setup` key on existence, so all three
    report healthy while scanning nothing. That is the live state of this vault
    (9 lines, 0 active), and it is the shape of defect this metric exists to
    make visible.

    Zero here is a real measurement, not an abstention, and that is safe because
    G1's bound is a FLOOR (`≥ 1`): an unloaded guard fails it rather than
    passing. Contrast `pending_age`, whose bound is a ceiling — there an
    unmeasurable zero would pass, so it abstains instead.
    """
    p = pii_patterns_path if pii_patterns_path is not None else _PII_PATTERNS_PATH
    if not p.is_file():
        return {"pii_active_patterns": 0, "_file_present": False}
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # ABSTAIN, don't raise. This file is per-machine and user-managed: a
        # latin-1 regex or a permission change would otherwise propagate out of
        # `metrics()` → `baseline.generate()` → `verify_against()` and abort
        # every OTHER metric and every global invariant along with it.
        return {"_file_present": True, "_unreadable": True}
    active = 0
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            active += 1
    return {"pii_active_patterns": active, "_file_present": True}


# ── 5.3c seeded probe — liveness by execution (RFC 0009 G1) ──────────────────

_HOOK_PATH = (Path(__file__).resolve().parents[3]
              / "scripts" / "git-hooks" / "pre-commit")
_PROBE_TOKEN = "SEEDED-PII-XYZZY"


def seeded_probe_blocked(*, hook: Path | None = None) -> int | None:
    """1 iff the shipped pre-commit guard, executed in a hermetic scratch repo,
    BLOCKS a staged seeded match AND passes a clean stage; 0 if either half
    fails (a live defect — the guard is present but not guarding); None when
    the probe cannot run (no git, no hook script).

    This is the half of G1's bar a pattern *count* cannot carry (§7): one junk
    regex makes `pii_active_patterns ≥ 1` true while blocking nothing real.
    Execution is hermetic by construction — `ATELIER_PII_PATTERNS` points the
    hook at a probe-local fixture and HOME is isolated, so the user's real
    pattern file is never read and its content never influences the score.

    0 vs None follows §5.4 exactly: "ran and failed to block" is a REAL
    measurement (a floor bound must be able to fail on it); "could not run"
    abstains (the leaf is omitted, never a fabricated pass/fail).
    """
    import os
    import shutil
    import subprocess
    import tempfile
    hook_src = hook if hook is not None else _HOOK_PATH
    if not hook_src.is_file() or shutil.which("git") is None:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir()
            # Hermetic environment — review [MUST]: a copied caller env leaks.
            # Inherited GIT_DIR/GIT_INDEX_FILE (standard inside any git hook)
            # made the scratch `git init`/`add` target the CALLER'S repo — a
            # write to a repo atelier does not own; an inherited
            # ATELIER_MAX_STAGED_BYTES tripped layer 1 on the clean stage and
            # fabricated a 0 against a healthy guard. So: scrub every GIT_*,
            # drop XDG_CONFIG_HOME (git prefers $XDG_CONFIG_HOME/git/config
            # over $HOME, defeating HOME isolation) and the size knob, and
            # sever global/system git config explicitly.
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("GIT_")}
            env.pop("XDG_CONFIG_HOME", None)
            env.pop("ATELIER_MAX_STAGED_BYTES", None)  # pin layer 1 to default
            env["HOME"] = str(root)                    # isolate ~/.atelier
            env["GIT_CONFIG_NOSYSTEM"] = "1"
            env["GIT_CONFIG_GLOBAL"] = os.devnull
            patterns = root / "probe_patterns.txt"
            patterns.write_text(f"# probe fixture\n{_PROBE_TOKEN}\n",
                                encoding="utf-8")
            env["ATELIER_PII_PATTERNS"] = str(patterns)

            def _git(*args: str) -> None:
                subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                               capture_output=True, timeout=10)

            def _hook() -> int:
                # No check=True here — a nonzero exit is the MEASUREMENT
                # (blocked), never an exception; only scaffolding may raise.
                return subprocess.run(["bash", str(hook_src)], cwd=repo,
                                      env=env, capture_output=True,
                                      timeout=10).returncode

            _git("init", "-q")
            (repo / "leak.md").write_text(f"body with {_PROBE_TOKEN}\n",
                                          encoding="utf-8")
            _git("add", "leak.md")
            blocked = _hook() != 0
            (repo / "leak.md").write_text("clean body\n", encoding="utf-8")
            _git("add", "leak.md")
            clean_passes = _hook() == 0
            return 1 if (blocked and clean_passes) else 0
    except Exception:
        return None                                    # probe environment broke


# ── 5.5 lens surface coverage ───────────────────────────────────────────────

def _declared_surfaces() -> list[dict[str, Any]] | None:
    """The declared surface list, or None when it cannot be read.

    None rather than an exception: this file is a new hard dependency of
    `baseline.generate()`, which `verify.verify_against()` calls. A raised
    `FileNotFoundError` here would abort the entire verification — the four
    unrelated metrics and every global invariant with it — where abstaining on
    one key is the specified behaviour (§5.4).
    """
    try:
        data = yaml.safe_load(_SURFACES_YAML.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    surfaces = data.get("surfaces")
    return list(surfaces) if isinstance(surfaces, list) else None


def lens_param_present() -> dict[str, Any] | None:
    """Content-returning MCP surfaces whose handler **accepts** a `lens`
    argument — a signature-level fact, and named for exactly that.

    An earlier revision called this `lens_surface_coverage` and RFC 0009 §5.5
    defined it as surfaces that "accept **and honour**" a lens. Only the first
    half is decidable from a signature, and the gap is not academic: with a
    contract bound of `covered = 6`, adding `lens: str = "dev"` to five handlers
    and discarding the value satisfies INTENT, ENVELOPE and INVARIANT while
    `session_bootstrap` still pushes personal claims into every dev session.
    That is the vacuous PASS this program exists to prevent, on the one counter
    §3.2 rule 2 singles out — so the metric is named for what it can prove, and
    **honouring is a behavioural gate G3 must add** (the shape already exists:
    `verify._check_dev_lens_no_personal` calls the retrieval path and inspects
    what comes back).

    The denominator stays schema data (§3.2 rule 2) so it cannot be shrunk to
    meet a bound; the numerator is introspected, so the yaml cannot claim a
    parameter the code does not have. Returns None (→ key omitted) when the
    declaration is unreadable.
    """
    from .. import tools as _tools
    declared = _declared_surfaces()
    if declared is None:
        return None
    present: list[str] = []
    absent: list[str] = []
    unimplemented: list[str] = []
    for entry in declared:
        name = str(entry.get("name") or "")
        handler = getattr(_tools, f"_h_{name}", None)
        if handler is None:
            # A DECLARED surface with no handler is a typo in the yaml, not a
            # missing lens. Folding the two together would cap `covered`
            # permanently with nothing in the output to say why.
            unimplemented.append(name)
            continue
        params = inspect.signature(handler).parameters
        (present if "lens" in params else absent).append(name)
    total = len(present) + len(absent) + len(unimplemented)
    return {"covered": len(present), "total": total,
            "unimplemented": len(unimplemented),
            "_present": sorted(present), "_absent": sorted(absent),
            "_unimplemented": sorted(unimplemented)}


# ── the block ───────────────────────────────────────────────────────────────

def metrics(*, as_of: date | None = None, vault: Path | None = None,
            pii_patterns_path: Path | None = None,
            probes_path: Path | None = None) -> dict[str, Any]:
    """The `metrics` block of a baseline (RFC 0009 §5).

    Two shape rules follow from §3.4, which makes ENVELOPE default-deny over
    "the leaf keys under `metrics`":

    - **Capture metadata does not live here.** `as_of` changes on every round
      baseline by construction, and §3.5 requires a waiver to carry a *numeric*
      bound — so an `as_of` leaf would trip default-deny on every run with no
      legal waiver shape. It belongs beside `captured_date` at the top level.
    - **Diagnostic leaves are `_`-prefixed** (`_present`, `_absent`,
      `_file_present`). They are lists and booleans, which cannot carry a
      numeric bound either. The prefix is a READABILITY convention, not the
      exclusion mechanism: §5.1.1 makes the rule "`_`-prefixed **or**
      non-numeric", because the frozen `0006-baseline.json` already carries
      unprefixed non-numeric leaves (`eval.engine`, `eval.paraphrase.stale`)
      that can never be renamed.

    `cross_project_noise` (§5.4) is present iff its out-of-tree probe fixture
    exists (`~/.atelier/fixtures/project_probes.json`, §5.6) — on a fresh
    clone the key is simply absent, so CI neither fails nor reports green, and
    a contract naming it there raises rather than reading a fabricated zero.
    Any counter that cannot measure omits its key the same way.
    """
    stamp = as_of or datetime.now(UTC).date()
    gl = guard_liveness(pii_patterns_path=pii_patterns_path)
    probe = seeded_probe_blocked()
    if probe is not None:                # abstain → leaf omitted (§5.4)
        gl["seeded_probe_blocked"] = probe
    out: dict[str, Any] = {
        "promote_eligible": promote_eligible(vault=vault),
        "pending_age": pending_age(as_of=stamp, vault=vault),
        "guard_liveness": gl,
    }
    lens = lens_param_present()
    if lens is not None:
        out["lens_param_present"] = lens
    dangling = dangling_links()
    if dangling is not None:
        out["dangling_links"] = dangling
    dnew = dangling_new()
    if dnew is not None:
        out["dangling_new"] = dnew
    noise = cross_project_noise(fixture_path=probes_path)
    if noise is not None:
        out["cross_project_noise"] = noise
    return out
