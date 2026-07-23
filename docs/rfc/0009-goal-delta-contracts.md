# RFC 0009 — Goal-driven delta contracts: verifying deliberate reductions

| | |
|---|---|
| **Status** | Draft (proposed 2026-07-23) |
| **Scope** | the verification protocol for the work that remains after RFC 0006's four pillars — a **delta contract** (declare the intended change, then gate on it), a third snapshot class (the per-run *round baseline*), five new census counters, and a **convergence loop** that re-verifies after a fix instead of failing once. Adds the `goal` command as the operator surface. |
| **Builds on** | RFC 0006 (the rubric-gated protocol, `verify.py`, `baseline.py`, `census.py`, `scripts/workflows/memory-pillar.mjs`), RFC 0008 (absorb perimeter — the source of several goals here) |
| **Revises** | RFC 0006 §6. Its *baseline-diffing* gates are all monotone ("did not shrink / did not regress"), which cannot express a deliberate reduction. This RFC adds an orthogonal axis and defines the one narrow way a contract may supersede a monotone invariant (§3.3); it removes no invariant. |
| **Schema** | no node-schema change. `census.py` gains counters; the `lens_surface_coverage` denominator is schema data (hard rule #3). A contract is a committed JSON artifact under `docs/goals/`; probe fixtures live **out of tree** (§5.6). |

---

## 1. Summary & thesis

RFC 0006 P0 built a real verification harness: a frozen baseline, an independent
verifier, snapshot/rollback, and a rubric registry. All four pillars shipped
through it. The remaining backlog — five problem families surfaced by the RFC 0008
review — cannot be verified by that harness, for one structural reason:

> **Every *baseline-diffing* gate in `verify.py` is monotone.**
> `_check_no_data_loss` fails when a node kind shrank.
> `_check_no_omission_regression` fails when `visible` fell.
> `_metric_not_regressed` fails when a score dropped. The bar is "nothing got
> smaller."

(The four *property* gates — `_check_lens_coverage`, `_check_manifest`,
`_check_forgets_flag_only`, `_check_dev_lens_no_personal` — never read
`before`/`after` at all. They are the existing precedent for a non-monotone
check, and §5.4 borrows their shape. They also already carry the disease this RFC
is about: `_check_dev_lens_no_personal` documents that it "passes vacuously" when
its probe returns nothing.)

But the remaining work is **subtractive**. Narrowing the promote predicate is
*meant* to take eligibility from 830 to ~23. Narrowing auto-pass is *meant* to
reduce the passed pool. Extending the lens to five more surfaces is *meant* to
return fewer rows. A monotone verifier meets a deliberate reduction in one of two
ways, both useless:

- it **fails spuriously**, because the intended reduction looks like data loss; or
- it **passes vacuously**, because the quantity that actually changed is not in
  the baseline at all.

The second is the dangerous one. `promote_eligible` is not derivable from today's
baseline even in principle: `census.py` tallies `domain`/`ac_status`/`surfacing`
as independent *marginals*, while `is_promote_eligible` needs the *joint* of
`surfacing × sensitivity × ac_status` — and `sensitivity` is not tallied at all.
So today's verifier would return PASS on the largest remaining goal while
observing nothing.

> **Thesis.** Replace "prove nothing shrank" with "prove **exactly this** changed,
> and nothing else did." A goal declares a machine-checkable **delta contract**
> before any code is written; the verifier scores three layers — did the intended
> change happen (INTENT), did anything outside it move (ENVELOPE), did the
> never-break bar hold (INVARIANT). ENVELOPE is strictly stronger than the
> monotone gate it supplements: an *unintended* reduction, which a monotone check
> passes when it is not measured, fails the envelope.

No new judgement machinery. The contract is data; the checks are pure functions
of (before, after); the loop is the existing `memory-pillar.mjs` shape with a
verify→fix edge added.

---

## 2. Measurement — the state this RFC is designed against

Taken 2026-07-23 against the live vault (4,477 claims), before any change here.

```
promote-eligible            830   knowledge 807 (born-accepted) · operational 23
  distinct upstream sources 117   over all 830 (≈7.1 claims/source); the
                                  knowledge 807 alone span 103 (≈7.8)
  731 of the 830 minted 2026-06; the 807 knowledge claims carry no `project`

pending claims               36   all domain: operational
  age in days min/med/max  0 / 3 / 38

operational claims          233   across 25 distinct projects
  belonging to this repo     30   → in a session on this repo, 87% of the
                                    operational corpus is other projects' work

pii_patterns.txt        9 lines   ACTIVE (non-comment) patterns: 0
lens-scoped MCP surfaces  1 of 6   only `recall` (tools.py:507)

frozen baseline      2026-07-04   19 days stale; claims 4,262 → 4,477 (+215)
```

Three of these numbers decide the design:

1. **`promote_eligible` is absent from the census** and not derivable from it
   (§1). The single most important quantity for the largest goal cannot be
   observed today. Counters come first (§5).
2. **`pending` max age is 38 days, not the count 36.** The RFC 0008 reviewer's
   "the pending queue is where value dies" is a *staleness* claim. Gating on the
   count would let a workflow pass by draining recent items while the 38-day tail
   rots. The metric must be the age distribution.
3. **87% cross-project noise, and lens covers 1 of 6 surfaces.** Pillar ③'s lens
   axis is `domain`, and `dev` *includes* `operational` — so the shipped lens
   cannot touch this. It needs a second axis (§5.4), and a metric that a
   `_check_dev_lens_no_personal`-shaped gate can read.

---

## 3. The delta contract

A contract is a small JSON document committed under `docs/goals/<id>.json`
**before implementation begins**:

```json
{
  "id": "G2-promote-predicate",
  "goal": "Narrow promote eligibility to the operational lane.",
  "baseline": "docs/rfc/0009-baseline.json",
  "pins": {
    "before_sha256": "9f2c…",
    "captured_at_head": "2a43ec4…",
    "fixture_sha256": null
  },
  "intent": [
    {"metric": "promote_eligible.total", "from": 830, "to": {"max": 30}},
    {"metric": "promote_eligible.by_domain.knowledge", "to": {"eq": 0}}
  ],
  "envelope": {"mode": "default-deny", "waivers": []},
  "supersedes": [],
  "rubric": "P0"
}
```

- **INTENT** — the declared change. Each entry names a metric and a bound
  (`eq` / `max` / `min` / `delta`). A goal that cannot state one is not ready to
  be a goal; it is still a discussion (§7).
- **ENVELOPE** — **default-deny, not an opt-in list.** Every metric in the
  namespace that INTENT does not name must be unchanged, plus
  `vault.content_fingerprint` (§5.7). A waiver names a metric and a reason and is
  accepted by the critic.

  An enumerated envelope makes the thesis ("nothing else moved") a property of
  the contract *author*, which is the §3.2 problem one level up: a G3 contract
  listing only lens metrics leaves `promote_eligible`, `pending_age`, and
  `guard_liveness` unguarded. This matters more after §3.3 moves the counters out
  of `census`, since that also removes the incidental monotone floor INV-1 was
  giving them.
- **INVARIANT** — not authored in the contract. The global gates apply to every
  run; a contract may only *supersede* a named clause under §3.3, never soften
  one silently.
- **`pins`** — the integrity roots (§3.1, §4.1, §5.6). They live *in the
  contract* because the contract is the only git-pinned artifact in the run.

### 3.1 Freezing the target

`verify.verify_against` refuses a baseline with uncommitted changes. That guard is
**weaker than it reads**, and the contract must not simply inherit it:

- `atelier verify --allow-uncommitted` is a **public CLI flag** (`cli.py:555`),
  not a test-only affordance — `verify.py`'s module docstring claiming "Skippable
  only in tests" is false, and is corrected alongside this RFC.
- `git status --porcelain` proves a file is **clean**, not **old**. Committing is
  free: widen the bound, `git commit`, verify → clean tree → PASS. RFC 0006 §6
  specified "dirty **or newer than the tag**"; only the first half shipped.

So the contract is pinned by **content and ancestry**, not cleanliness:

1. The verifier resolves the contract's git blob sha for `docs/goals/<id>.json`
   at the run's recorded head and re-reads it with `git show <blob-sha>` — never
   from the working tree.
2. It **fails closed** if the blob is absent from the repo, or if the commit that
   introduced it is not an ancestor of the implement stage's base.
3. `--allow-uncommitted` is refused for contract-mode verification. Tests set an
   explicit env var instead, so the escape hatch cannot be typed by an agent
   following a workflow prompt.

### 3.1.1 Where the pins live — and why not in a manifest

An earlier revision recorded the contract sha and the round-baseline hash in a
manifest under `~/.atelier/cache/goals/<id>/`. That reintroduces the hole it was
meant to close: the directory is outside every git repo and is not covered by
`snapshot create`, so a builder can rewrite the manifest and the artifact it
attests to in one step, and every recomputation agrees.

**The pins therefore live inside the contract**, which is the run's only
git-pinned artifact. The ordering works because Snapshot precedes Contract (§6):
the round baseline is captured first, and its hash and the capture-time `HEAD`
are written into the contract before it is committed.

That capture-time `HEAD` also supplies the missing ordering proof — nothing
otherwise forces Snapshot to precede Implement. The verifier requires it to be an
ancestor of the implement base and **not** to contain it, so a "before" captured
after the change is structurally detectable.

Without all of this the loop is self-grading with extra steps: a builder that
cannot hit its bound can widen it, or re-measure until it fits.

### 3.2 Freezing the *measurement* (the harder half)

Pinning the bound is worthless if the thing being measured is authored by the
party being graded. The counters in §5 ship in the same PR as the change they
score, so a builder that cannot move the number can redefine it. Concretely for
G2: leave `claims_io.is_promote_eligible` untouched and implement the counter as
`ac_status == 'passed'` only, dropping the born-accepted branch. It reports 23.
Every INTENT and ENVELOPE clause passes — on a vault where promote still proposes
830 claims.

Three rules close this:

1. **A counter is a thin wrapper over the production predicate**, never a
   re-implementation. `promote_eligible` calls `claims_io.is_promote_eligible`,
   the same function `promote.propose._eligible` uses; a divergence test asserts
   the counter equals `len(propose._eligible(limit=None))`.
2. **Any denominator is schema data, not a literal.** `lens_surface_coverage`'s
   "6" is defined nowhere in the repo today; it becomes a declared list of
   content-returning surfaces in `schema/data/` (hard rule #3), so a builder
   cannot reach 6/6 by redefining the surface set.
3. **The metric diff and any probe fixture are part of what the critic accepts**
   at the Contract stage — before implementation. A metric authored after the
   change is the same defect as a baseline regenerated after the change.

### 3.3 When an invariant is superseded

A contract cannot weaken a global invariant, but three goals are *defined* by
reducing what one of them measures — and left alone, the invariant makes them
unshippable through the harness that exists to ship them:

- **INV-4 vs G5.** `_check_no_omission_regression` fails when `surfacing.visible`
  falls, and `surfacing.audit` is computed over the **accepted pool**. Narrowing
  auto-pass shrinks that pool by design, so `total` and `visible` both fall.
- **INV-1 vs G2.** `_census_kind_totals` iterates the census's top-level keys as
  *kinds*. A counter landing at `census["promote_eligible"]` would silently become
  a monotone no-shrink gate on the exact quantity G2 must drive 830 → 23.

A third case is easy to miss: the `self_probe` and `paraphrase` recall invariants
are computed over that *same* accepted pool (`eval` enumerates it through
`store.iter_accepted_files`). So G5 can fail `_metric_not_regressed` for the same
structural reason it fails INV-4 — supersession must be able to name them too.

Therefore:

- **Invariants are computed over a fixed, named key set.** The new counters land
  in a sibling `metrics` block, never inside `census`, so INV-1 keeps meaning
  "graph nodes did not vanish." (Verified: `_census_kind_totals` is called only on
  `before["census"]`/`after["census"]`, and no other check walks the baseline's
  top level.)
- **Supersession is per-clause, not per-invariant.** An entry names the invariant
  *and the specific metric and direction* it releases — `INV-4 / surfacing.visible
  / may-fall`. INV-4 gates two quantities; releasing it wholesale for a fall in
  `visible` would silently stop gating `dark_count` as well.
- **The invariant→metric map is schema data**, not prose — the same hard-rule-#3
  argument §3.2 rule 2 makes for the `lens_surface_coverage` denominator. Without
  a declared mapping, "a `supersedes` entry with a matching INTENT bound" has no
  definition of *matching*: a contract could release `INV-1` while its exact
  INTENT clause is `lens_surface_coverage.covered = 6` and pass mechanically,
  disabling the no-data-loss gate for a run that never earned it.
- Each entry additionally requires an INTENT clause **bounding the same metric**,
  a one-line reason, and explicit critic acceptance.

---

## 4. Snapshots — three classes

RFC 0006 defined two. A convergence loop needs a third.

| class | lifetime | diffed? | restores | location |
|---|---|---|---|---|
| **data-safety** | permanent | never | the **vault** | `~/.atelier/snapshots/<ts>/` |
| **frozen baseline** | one program | yes | — | `docs/rfc/000N-baseline.json` |
| **round baseline** 🆕 | one goal run | yes | — | `~/.atelier/cache/goals/<id>/before.json` |

The frozen baseline answers *"have we drifted since the program began?"*. The
round baseline answers *"did this run do exactly what it claimed?"*. They cannot
be the same artifact: `0006-baseline.json` is 19 days and +215 claims stale, so a
23-claim intended delta measured against it is buried in unrelated drift.

**`0006-baseline.json` stays frozen** — it is the evidence that pillars ①–④ did
not regress, and rewriting it destroys that record. This RFC captures
`docs/rfc/0009-baseline.json` as its own program anchor, matching the existing
one-baseline-per-program convention (`0002-baseline.json`, `0006-baseline.json`).

**Ordering constraint:** the 0009 anchor is captured *after* §5's counters land,
not with this document. A baseline frozen before `promote_eligible` exists cannot
observe the goal it is meant to anchor, and would have to be re-frozen
immediately — which is exactly the "regenerate the before, after the fact" move
§3.1 forbids.

### 4.1 The round baseline needs the same protection as the contract

`before.json` sits in `~/.atelier/cache/`, which is outside every git repo and is
**not** included in `snapshot create` (it tars `config.yaml`, `voices`, `secrets`,
`pii_patterns.txt` only). As specified so far it is a plain file the implement
stage can rewrite — so the §3.1 argument applies with equal force to the artifact
that actually decides the delta, and was not applied. Failure: the builder
overshoots (eligibility → 5, and 40 claims lost), re-captures the round baseline
after the change, and `census.claim.total unchanged` compares the after-state to
itself → PASS with 40 claims gone.

So the round baseline is **content-hashed at capture**, and that hash goes into
the contract's `pins.before_sha256` (§3.1.1) — not into a sibling manifest, which
would leave the attestation as writable as the thing it attests to. The verifier
recomputes the hash and fails closed on a mismatch.

### 4.2 Capture conditions, and what rollback actually covers

- **Reindex before capture.** `census.census()` is projection-first, and the
  verify stage runs `atelier reindex` before measuring. A before taken on a stale
  DB and an after taken post-reindex differ by projection lag alone.
- **Pin the embedding env.** `baseline.generate()` runs `eval.run()`, whose
  determinism holds only per `ATELIER_EMBED`. `engine_unchanged` is a *warn* in
  RFC 0006; for goal runs it is **promoted to a gate**, since an envelope over
  eval metrics with a flipped engine fails spuriously and without them is blind.
- **Freeze the as-of date.** `pending_age` is wall-clock-derived — the one new
  metric whose value changes on a later day with identical commits. The round
  baseline records `as_of`, and the counter takes it as a parameter, so evaluation
  is reproducible and an `unchanged: true` envelope over it is satisfiable.
- **Rollback is two mechanisms, not one.** `snapshot restore` returns the
  **vault**; it does not touch this repo. G2/G3/G5 are code changes, so the
  round-3 abort is `git` (discard the branch) **plus** `snapshot restore` if the
  run mutated vault content. §6 states both.

---

## 5. New metrics

All five ship beside `census.py` under the same projection-first /
filesystem-fallback discipline, and land in a **`metrics` block** of the baseline
— never inside `census` (§3.3).

### 5.1 `promote_eligible{total, by_domain}`
**Extend `projection_counts.promote_eligible()`, do not write a new counter.** It
is already the thin wrapper over `claims_io.is_promote_eligible` that §3.2 rule 1
prescribes, already carries the projection/filesystem parity discipline §11.1
asks for, and lacks only the `by_domain` split. A second counter beside
`census.py` would be a duplicate definition — precisely the divergence rule 1
exists to prevent. What is missing today is its presence in the *baseline*, not
its existence (§1).

### 5.2 `pending_age{count, p50, max}`
Days between each `ac_status: pending` claim's `created_at` and the round
baseline's frozen `as_of` (§4.2). Gates on the tail, not the count (§2, point 2).

### 5.3 `guard_liveness{pii_active_patterns, ...}`
Counts *active* (non-comment, non-blank) lines, not file existence. RFC 0008 §6
specified the absent-file case deliberately (a no-op pass); the case it left
**unspecified** is a file that exists with zero active lines — which both
enforcement points and `scripts/setup` report healthy on. This metric closes that
gap. Because the file is untracked and per-machine, the count alone is not
auditable by anyone else, so G1 pairs it with a seeded-match probe (§7).

### 5.4 `cross_project_noise{project, foreign_ratio, returned}`
Run a dev-session recall for a given project; report the fraction of returned
claims whose `project` is some other project. It generalizes
`_check_dev_lens_no_personal` from the `domain` axis to `project` — **including
that gate's known defect**, which must not be inherited: a probe returning nothing
gives 0/0, and any `≤ x` bound passes while the lens returns nothing at all. So
the metric carries `returned`, and a **minimum-yield precondition** (`returned ≥
20`) is enforced **inside metric evaluation**, not left to a contract clause the
author may omit — below the threshold the metric emits *no* `foreign_ratio` key,
so §8.1.3's raise-on-unknown-key fires. G3's bound is a number: `foreign_ratio ≤
0.15` against today's measured 0.87.

**Abstention is key-absence, never a zero.** The natural implementation of "no
fixture" or "too few hits" is `{"returned": 0, "foreign_ratio": 0.0}` — which
*passes* a `≤ 0.15` bound and reports green on a lens that returns nothing. Every
abstaining metric in this RFC omits its key instead, which is the only encoding
that composes with §8.1.3.

### 5.5 `lens_surface_coverage{covered, total}`
Content-returning MCP surfaces that accept and honour a `lens` argument — today
1, and the denominator comes from a declared list in `schema/data/` (§3.2 rule 2),
not from a literal in the counter.

### 5.6 Artifact PII posture (hard rule #1)

This RFC introduces two new committed artifact classes, and both can carry vault
strings if left unspecified:

- **Contracts (`docs/goals/*.json`) carry counts and metric names only** — never
  a project name, path, or claim statement drawn from the vault.
- **The §5.4 probe fixture lives out of tree**, at
  `~/.atelier/fixtures/project_probes.json`. An honest `(project, query)` fixture
  must name real projects and realistic session queries — precisely the
  adopter-specific identifiers hard rule #1 forbids in repo content. The repo
  holds only anonymised ids (`p1`, `p2`) and the fixture's shape; the counter
  abstains by *omitting its key* (§5.4) when the fixture is absent, so CI on a
  fresh clone neither fails nor silently reports green.

  **Out of tree means unpinnable by §3.1 — so pin it by content.** The fixture is
  the one measurement input §3.2 rule 3 requires frozen that a blob sha cannot
  reach. Without a pin: the round baseline captures `foreign_ratio` at 0.87 with
  fixture v1; during Implement the builder rewrites the queries to ones whose hits
  are same-project; Verify reads `0.05` with `returned: 25` and both INTENT and
  minimum-yield pass with no lens change shipped. So the fixture's sha256 goes
  into `pins.fixture_sha256` in the committed, counts-only contract, and the
  verifier recomputes it.

  The fixture directory is also added to the snapshot's durable set
  (`config.yaml`, `voices`, `secrets`, `pii_patterns.txt` today), so a round-3
  restore can return a mutated fixture to its pre-run state.

Note the ordering hazard: the pre-commit PII guard is what would catch a slip
here, and G1 is what makes that guard live. Until G1 lands, these two rules are
enforced by review.

### 5.7 `vault.content_fingerprint`

The ENVELOPE primitive: a hash over `(path, sha256(content))` for every vault
markdown file — **content only, no `mtime`**, excluding derived files
(`INDEX.md`, `MEMORY.md`) the way `census._fs_rows` already does. The verify stage
runs `reindex`, which rewrites `INDEX.md` and can bump `mtime_ns` on files whose
content never changed; an `mtime`-sensitive fingerprint would fail every run.

`_check_forgets_flag_only` is the shape precedent, not a drop-in: it hashes
`(path, mtime_ns, bytes)` twice **inside one process, milliseconds apart**, which
is why `mtime` is safe there and not here.

> **Cost, measured:** the live vault is 7,261 markdown files / 13.4 MB; a full
> content fingerprint takes **~1 s** (1.01 s measured cold; sub-second warm — the
> spread is page cache, so budget for the cold figure). The slow half of a run is
> `baseline.generate()` → `eval.run()`. An earlier draft proposed weakening this
> to "census + file-set hash" for speed — that is a same-count, same-path-set
> check, i.e. blind to exactly the content swap the envelope exists to catch, and
> it buys nothing.

---

## 6. The convergence loop

```
Snapshot    atelier snapshot create               (vault rollback point)
            reindex, then capture round baseline  (before.json + as_of)
Contract    author the delta contract  →  CRITIC must accept it
            pins: before.json hash, capture-time HEAD, fixture sha256
            commit it, plus the metric diff and the fixture (§3.2 rule 3)
            ← nothing is implemented before this
Implement   builder agent
Verify      independent verifier: INTENT + ENVELOPE + INVARIANT
            contract re-read via `git show`; pins recomputed and checked
      ├─ PASS ────────────────────────────────────────────────→ Ship
      ├─ FAIL, round < 3 → FIXER agent gets ONLY the failing checks → Verify
      ├─ FAIL, round = 3 → discard the branch (code) + snapshot restore if the
      │                    run mutated the vault → escalate, never merge
      └─ RAISE (broken pin, unknown metric key, absent blob) → HARD ABORT
                           does not consume a round, is never retried in-run,
                           and never reaches Ship
Ship        ship-pr (which runs its own independent review loop)
```

**A raise is not a FAIL.** A failing check means the change missed its target and
a fixer may try again. A raise means the *harness* cannot be trusted for this run
— a pin that does not match, a metric key that does not exist, a contract blob
that is not in the repo. Retrying inside the run would let a builder convert a
broken integrity check into three chances at a green one, so the run aborts and
escalates to a human. Amending the contract mid-run is therefore not a path: a new
contract is a new run, with a new round baseline.

Three properties, each deliberate:

- **The critic gates the contract, not the code.** A bad contract cannot be
  detected after the fact — by then the implementation defines the target. This is
  the cheapest stage to catch "this goal is not measurable yet," and under §3.2 it
  is also where the *measurement* is reviewed.

  The critic is load-bearing for three decisions — envelope waivers, `supersedes`
  entries, and the metric diff — so it is a **distinct agent from the builder**,
  on RFC 0006's independent-verifier principle, and its acceptance is recorded
  **in the committed contract** (a `critic` block naming what it accepted and
  why). Recording it anywhere untracked would inherit §3.1.1's problem: an
  attestation as writable as the thing it attests to.
- **The fixer receives failing checks only**, not the builder's narrative. Handing
  over the builder's own account of what it did reintroduces the self-grading the
  independent-verifier stage exists to prevent.
- **Three rounds, then abort.** Matching `ship-pr`'s existing non-convergence bar.
  A loop with no ceiling is how an agent grinds a budget against a goal that was
  mis-specified in stage 2.

---

## 7. Goal catalogue — what is and is not goal-able

Applying the "can you state an INTENT bound that cannot be satisfied without
achieving the goal?" test to the five open problem families:

| goal | INTENT bound | verdict |
|---|---|---|
| **G1** L1 lint + PII guard liveness | `lint.L1 = 0`; `pii_active_patterns ≥ 1` **and** a seeded-match probe blocks | ✅ leads on the repo-local half |
| **G2** promote predicate | `promote_eligible.total ≤ 30`, `.by_domain.knowledge = 0` | ✅ measurable |
| **G3** lens coverage + project axis | `lens_surface_coverage = 6/6`; `foreign_ratio ≤ 0.15` with `returned ≥ 20` | ✅ needs the §5.6 fixture |
| **G4** pending review surface | the surface returns **all** pending claims with ages, asserted equal to `pending_age.count`/`.max` | ⚠️ tooling only — the 36 judgements stay human |
| **G5** auto-pass narrowing; wiki-link repair | passed-pool delta (exact, with INV-4 in `supersedes`); dangling-link count → 0 | ✅ mechanical |
| — **stale status-snapshot claims** | none statable | ❌ **excluded** |

Two rows deserve their reasoning stated:

**G1's bound is deliberately not just the pattern count.** `pii_active_patterns ≥
1` is satisfied by appending one junk regex, and the file is untracked and
per-machine — so a count-only PASS is reproducible by nobody, on a run where the
builder and the "independent" verifier share a `$HOME`. The lint half is
repo-local and auditable, and the seeded-match probe (a fixture string that must
be *blocked*) is what makes the guard's liveness mean something.

**The excluded row is the important one.** "Is this claim permanently false now?"
("migration COMPLETE") has no labelled set and no derivable predicate. Inventing a
metric for it would produce a gate that passes on the wrong thing — the exact
vacuous-PASS failure this RFC exists to prevent. It stays a human review item
until a labelled probe set exists. G4 was nearly excluded for the same reason: an
earlier draft's bound was "a tool exists," which is satisfiable while the 38-day
tail rots. The bound above is checkable against the counter, which is what earns
it the ⚠️ rather than the ❌.

---

## 8. Sequencing

| phase | deliverable | gate |
|---|---|---|
| **G0** | this RFC + `0009-baseline.json`; counters (§5.1–5.3, 5.5, 5.7); `contract.py`; verify 3-layer + the §3.1/§4.1 pins; `goal` skill + workflow | the two-sided gate below |
| **G1** | L1 lint + PII guard liveness | contract G1 |
| **G2** | promote predicate | contract G2 |
| **G3** | lens coverage + the §5.6 fixture | contract G3 |
| **G4** | pending review surface; auto-pass narrowing; wiki-links | contracts G4/G5 |

**G1 before G2 is deliberate.** The harness runs first against the *smallest*
problem, not the most valuable one. If the contract model is wrong, that surfaces
on a five-line change rather than on a predicate that reshapes 807 claims'
eligibility.

### 8.1 The G0 gate is two-sided, and the failing side runs through the vault

A verifier that passes an unchanged vault proves only that it does not fire
spuriously; a verifier that fails an injected delta proves it can fire at all.
Both are required — but the second must be injected **into the vault**, not into a
synthetic after-state dict. Injecting into a dict exercises only the pure
`evaluate(contract, before, after)` function and never the census → metric
namespace path, so a counter hard-wired to `0` passes *both* sides: the no-op run
PASSes, and the synthetic test FAILs on a hand-written `{"total": 900}`.

So the gate is:

1. an unchanged vault PASSes;
2. a **real** injected delta (mint a throwaway claim; flip one `ac_status`),
   measured end-to-end through the same code path a real run uses, FAILs the
   clause it violates. This requires one small fix first: `baseline.generate()`
   passes `vault` to `eval.run()` and `census.census()` but calls
   `surfacing.audit()` with no vault argument, and `audit`/`snapshot` resolve
   `_vault_root()` internally — so an injection into a temp vault cannot move
   `surfacing.*`. G0 parameterizes `audit()`; the alternative, minting into the
   live vault behind the data-safety snapshot, is not worth the blast radius for
   a self-test; and
3. `evaluate` **raises** on an INTENT/ENVELOPE metric key absent from either
   snapshot. There is a live precedent for why: `_metric_not_regressed._get`
   returns `0.0` for an unresolved path, so a contract naming
   `promote_eligible.knowledge` when the counter emits
   `promote_eligible.by_domain.knowledge` resolves to absent → 0 → `{"eq": 0}`
   **passes**, proving nothing.

RFC 0008's PII defect was a one-sided check that had never been shown to fail.

---

## 9. Non-goals

- **Autonomous merging of a goal.** A goal run ends at `ship-pr`, which keeps its
  own independent review and human-visible PR. The loop converges the *change*; it
  does not remove the review.
- **Making `project` a recall filter by default.** RFC 0005's recall prior ranks
  within a tier and does not silo; §5.4 measures noise so an explicit lens (G3)
  can scope it. Nothing here changes the default path.
- **A metric for claim staleness/truth-decay** (§7). Excluded until labelled.
- **Replacing the RFC 0006 rubrics.** They keep working for additive change; this
  RFC adds an axis beside them and touches one invariant only through §3.3.
- **Draining any queue by machine.** G4 ships the *surface*; the 36 pending
  judgements stay human.
- **Committing anything derived from vault strings** (§5.6).

---

## 10. Risks

- **Contract gaming.** *Mitigation*: §3.1 (blob-sha + ancestry, no
  `--allow-uncommitted`) **and** §3.2 — pinning the bound alone is insufficient
  when the grader's own measurement ships in the same PR.
- **A measurable goal that measures the wrong thing.** The `pending` count-vs-age
  case (§2, point 2) and the earlier G4 bound (§7) are the live examples. *Mitigation*:
  the critic's one job is to reject a bound satisfiable without achieving the goal.
- **Vacuous pass inherited from the precedent.** §5.4 copies a gate that documents
  its own vacuous-pass. *Mitigation*: `returned` is part of the metric and the
  minimum-yield precondition is a gate.
- **Two frozen baselines now exist.** A run diffing the wrong one gets meaningless
  results. *Mitigation*: the contract names its baseline explicitly; no default.
- **Envelope scope.** Fingerprinting is cheap (~1 s, §5.7); the real hazard is
  *what* it covers — `mtime` and derived files make it fail spuriously, and
  weakening it to a path-set check makes it blind.

---

## 11. Verification

1. **Counter parity** — each new counter agrees between the projection and the
   filesystem fallback on a reindexed vault (`tests/test_census.py` discipline).
2. **Counter–predicate divergence** — `promote_eligible.total` equals
   `len(promote.propose._eligible(limit=None))`; the test fails if the counter is
   re-implemented rather than wrapped (§3.2 rule 1).
3. **Contract evaluation is pure** — `evaluate(contract, before, after)` performs
   no I/O and no mutation, and **raises** on an unknown metric key (§8.1.3).
4. **Two-sided harness gate** — §8.1, with the failing side injected into the
   vault and measured end-to-end.
5. **Freeze guards** — verification against a contract whose blob sha is absent,
   whose commit is not an ancestor of the implement base, or whose round baseline
   hash mismatches, all raise. `--allow-uncommitted` is refused in contract mode.
6. **Invariant supersession** — a `supersedes` entry without a matching INTENT
   bound is rejected at the Contract stage; INV-1 is proven not to see the new
   `metrics` block (§3.3).
7. **Fingerprint stability** — a `reindex` that rewrites `INDEX.md` and touches
   `mtime` without changing content leaves `vault.content_fingerprint` unchanged;
   a one-byte edit to one claim changes it (§5.7).
8. **Reproducibility** — with `as_of` frozen, re-running the verifier on identical
   commits a day later yields an identical verdict (§4.2).
9. **Loop termination** — the workflow aborts and escalates at round 3 rather than
   looping or merging, and restores both mechanisms (§4.2).
10. **Full suite green** — `ATELIER_EMBED=off python3 -m pytest -q` (731 at the
    time of writing).
