# RFC 0009 — Goal-driven delta contracts: verifying deliberate reductions

| | |
|---|---|
| **Status** | Draft (proposed 2026-07-23) |
| **Scope** | the verification protocol for the work that remains after RFC 0006's four pillars — a **delta contract** (declare the intended change, then gate on it), a third snapshot class (the per-run *round baseline*), five new census counters, and a **convergence loop** that re-verifies after a fix instead of failing once. Adds the `goal` command as the operator surface. |
| **Builds on** | RFC 0006 (the rubric-gated protocol, `verify.py`, `baseline.py`, `census.py`, `scripts/workflows/memory-pillar.mjs`), RFC 0008 (absorb perimeter — the source of several goals here) |
| **Revises** | RFC 0006 §6. Its gates are all *monotone* ("did not shrink / did not regress"), which cannot express a deliberate reduction. This RFC adds an orthogonal axis; it removes no existing invariant. |
| **Schema** | no node-schema change. `census.py` gains counters; a contract is a committed JSON artifact under `docs/goals/`, not a graph node. |

---

## 1. Summary & thesis

RFC 0006 P0 built a real verification harness: a frozen baseline, an independent
verifier, snapshot/rollback, and a rubric registry. All four pillars shipped
through it. The remaining backlog — five problem families surfaced by the RFC 0008
review — cannot be verified by that harness, for one structural reason:

> **Every gate in `verify.py` is monotone.** `_check_no_data_loss` fails when a
> node kind shrank. `_check_no_omission_regression` fails when `visible` fell.
> `_metric_not_regressed` fails when a score dropped. The bar is "nothing got
> smaller."

But the remaining work is **subtractive**. Narrowing the promote predicate is
*meant* to take eligibility from 830 to ~23. Narrowing auto-pass is *meant* to
reduce the passed pool. Extending the lens to five more surfaces is *meant* to
return fewer rows. A monotone verifier meets a deliberate reduction in one of two
ways, both useless:

- it **fails spuriously**, because the intended reduction looks like data loss; or
- it **passes vacuously**, because the quantity that actually changed is not in
  the baseline at all (`promote_eligible` is measured nowhere in `census.py`).

The second is the dangerous one — it returns PASS while measuring nothing, which
is precisely the failure mode RFC 0008 M4 already hit once (§2: the PII guard
keys on file *existence* and reports healthy while scanning zero patterns).

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
  distinct upstream sources 103   ≈ 8 claims per source; 731 of 830 minted 2026-06
  claims carrying `project`  23   the 807 knowledge claims carry none

pending claims               36   all domain: operational
  age in days min/med/max  0 / 3 / 38

operational claims          233   across 25 distinct projects
  belonging to atelier       30   → in an atelier session, 87% of the
                                    operational corpus is other projects' work

pii_patterns.txt        9 lines   ACTIVE (non-comment) patterns: 0
lens-scoped MCP surfaces  1 of 6   only `recall` (tools.py:507)

frozen baseline      2026-07-04   19 days stale; claims 4,262 → 4,477 (+215)
```

Three of these numbers decide the design:

1. **`promote_eligible` is absent from the census.** The single most important
   quantity for the largest goal is not in the baseline, so today's verifier
   cannot observe the change at all. Counters come first (§5).
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
  "intent": [
    {"metric": "promote_eligible.total", "from": 830, "to": {"max": 30}},
    {"metric": "promote_eligible.knowledge", "to": {"eq": 0}}
  ],
  "envelope": [
    {"metric": "census.claim.total", "unchanged": true},
    {"metric": "vault.file_fingerprint", "unchanged": true}
  ],
  "rubric": "P0"
}
```

- **INTENT** — the declared change. Each entry names a metric and a bound
  (`eq` / `max` / `min` / `delta`). A goal that cannot state one is not ready to
  be a goal; it is still a discussion (§7).
- **ENVELOPE** — what must *not* move. Expressed over the same metric namespace
  plus `vault.file_fingerprint`, the `(path, mtime, bytes)` set already used by
  `verify._check_forgets_flag_only` to catch a same-count content swap.
- **INVARIANT** — not in the contract. The global gates (INV-1..4) apply to every
  run and a contract cannot weaken them; naming a rubric only *adds* checks.

### 3.1 The freeze rule (the integrity guard)

`verify.verify_against` already refuses to run against a baseline with
uncommitted changes, so nobody can regenerate the "before" *after* a change and
diff against themselves. **The contract inherits that rule.** It is committed and
hashed before the implement stage; the verifier reads it from `git show`, not
from the working tree.

Without this, the loop is self-grading with extra steps: a builder that cannot
hit its bound can widen the bound. The whole value of a separate verifier
evaporates if the target is writable by the thing being graded.

---

## 4. Snapshots — three classes

RFC 0006 defined two. A convergence loop needs a third.

| class | lifetime | diffed? | restored? | location |
|---|---|---|---|---|
| **data-safety** | permanent | never | yes | `~/.atelier/snapshots/<ts>/` |
| **frozen baseline** | one program | yes | never | `docs/rfc/000N-baseline.json` |
| **round baseline** 🆕 | one goal run | yes | never | `~/.atelier/cache/goals/<id>/before.json` |

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

---

## 5. New metrics

All five extend `census.py` (or sit beside it under the same
projection-first/filesystem-fallback discipline) so they enter the baseline
automatically and become addressable from a contract.

### 5.1 `promote_eligible{total, by_domain}`
The predicate already exists (`claims_io.is_promote_eligible`,
`projection_counts.promote_eligible`); it is simply not in the census. Free to add
and it is the gate for the largest goal.

### 5.2 `pending_age{count, p50, max}`
Days since `created_at` for every `ac_status: pending` claim. Gates on the tail,
not the count (§2.2).

### 5.3 `guard_liveness{pii_active_patterns, ...}`
Counts *active* (non-comment, non-blank) lines, not file existence. This metric is
the direct fix for a defect the system already shipped: both enforcement points
key on existence and reported healthy against 0 patterns.

### 5.4 `cross_project_noise{project, foreign_ratio}`
Run a realistic dev-session recall for a given project and report the fraction of
returned claims whose `project` is some *other* project. This is the only metric
that can gate the cross-project work, and it needs a small probe fixture (a set of
`(project, query)` pairs) to be honest. It generalizes
`verify._check_dev_lens_no_personal` from the `domain` axis to the `project` axis.

### 5.5 `lens_surface_coverage{covered, total}`
A static count of content-returning MCP surfaces that accept and honour a `lens`
argument. Today 1 of 6. Cheap, and it makes "we extended the lens" falsifiable.

> **`project` stays a boost, not a gate — outside an explicit lens.** RFC 0005's
> recall prior ranks within a tier and does not silo; nothing here changes that
> default. §5.4 measures noise so a *lens* can optionally scope it; it does not
> propose making `project` a filter on the default path.

---

## 6. The convergence loop

```
Snapshot    atelier snapshot create           (rollback point)
            capture round baseline            (before.json)
Contract    author the delta contract  →  CRITIC must accept it
            commit + hash                     ← nothing is written before this
Implement   builder agent
Verify      independent verifier: INTENT + ENVELOPE + INVARIANT
      ├─ PASS ────────────────────────────────────────────→ Ship
      ├─ FAIL, round < 3 → FIXER agent gets ONLY the failing checks → Verify
      └─ FAIL, round = 3 → snapshot restore + escalate (never merge)
Ship        ship-pr (which runs its own independent review loop)
```

Three properties, each deliberate:

- **The critic gates the contract, not the code.** A bad contract cannot be
  detected after the fact — by then the implementation defines the target. This is
  the cheapest stage to catch "this goal is not measurable yet."
- **The fixer receives failing checks only**, not the builder's narrative. Handing
  over the builder's own account of what it did reintroduces the self-grading the
  independent-verifier stage exists to prevent.
- **Three rounds, then restore.** Matching `ship-pr`'s existing non-convergence
  bar. A loop with no ceiling is how an agent grinds a budget against a goal that
  was mis-specified in stage 2.

---

## 7. Goal catalogue — what is and is not goal-able

Applying the "can you state an INTENT bound?" test to the five open problem
families:

| goal | INTENT bound | verdict |
|---|---|---|
| **G1** PII patterns + guard liveness + L1 lint | `pii_active_patterns ≥ 1`; `lint.L1 = 0` | ✅ mechanical |
| **G2** promote predicate | `promote_eligible.total ≤ 30`, `.knowledge = 0` | ✅ measurable |
| **G3** lens coverage + project axis | `lens_surface_coverage = 6/6`; `foreign_ratio ≤ x` | ✅ needs §5.4 fixture |
| **G4** pending review surface | `pending_age.max` bounded by a *tool existing*, not by the queue draining | ⚠️ tooling only — the 36 judgements are human |
| **G5** auto-pass narrowing; wiki-link repair | passed-pool delta; dangling-link count → 0 | ✅ mechanical |
| — **stale status-snapshot claims** | none statable | ❌ **excluded** |

The exclusion is the important row. "Is this claim permanently false now?"
("Playwright migration COMPLETE") has no labelled set and no derivable predicate.
Inventing a metric for it would produce a gate that passes on the wrong thing —
the exact vacuous-PASS failure this RFC exists to prevent. It stays a human
review item until a labelled probe set exists.

---

## 8. Sequencing

| phase | deliverable | gate |
|---|---|---|
| **G0** | this RFC + `0009-baseline.json`; census counters (§5.1–5.3, 5.5); `contract.py`; `verify` 3-layer support; `goal` skill + workflow | a no-op goal PASSes vacuously **and** an injected fake delta FAILs |
| **G1** | PII patterns, guard liveness, L1 lint | contract G1 |
| **G2** | promote predicate | contract G2 |
| **G3** | lens coverage + `cross_project_noise` fixture | contract G3 |
| **G4** | pending review surface; auto-pass narrowing; wiki-links | contracts G4/G5 |

**G1 before G2 is deliberate.** The harness runs first against the *smallest*
problem, not the most valuable one. If the contract model is wrong, that surfaces
on a five-line change rather than on a predicate that reshapes 807 claims'
eligibility.

The G0 gate is two-sided on purpose. A verifier that passes an unchanged vault
proves only that it does not fire spuriously; a verifier that fails an injected
delta proves it can fire at all. RFC 0008's PII defect was a one-sided check that
had never been shown to fail.

---

## 9. Non-goals

- **Autonomous merging of a goal.** A goal run ends at `ship-pr`, which keeps its
  own independent review and human-visible PR. The loop converges the *change*; it
  does not remove the review.
- **Making `project` a recall filter by default.** §5.4 measures noise. Any
  gating happens inside an explicit lens (G3), never on the default path.
- **A metric for claim staleness/truth-decay** (§7). Excluded until labelled.
- **Replacing the RFC 0006 rubrics.** They keep working for additive change; this
  RFC adds an axis beside them.
- **Draining any queue by machine.** G4 ships the *surface*; the 36 pending
  judgements stay human.

---

## 10. Risks

- **Contract gaming.** A builder that cannot hit a bound could widen it.
  *Mitigation*: §3.1 — committed, hashed, read from git by the verifier.
- **Envelope cost.** Proving "nothing else moved" over 4,477 claims means
  fingerprinting the vault. `_check_forgets_flag_only` already does this for the
  accepted pool; scaled to the full vault it is the slow half of every run.
  *Mitigation*: fingerprint by census + file-set hash, not by content diff.
- **Stale-baseline confusion.** Two frozen baselines now exist. A run that diffs
  the wrong one gets meaningless results. *Mitigation*: the contract names its
  baseline explicitly; there is no default.
- **A measurable goal that measures the wrong thing.** The `pending` count vs age
  case (§2.2) is the live example. *Mitigation*: the critic stage's one job is to
  reject a bound that can be satisfied without achieving the goal.

---

## 11. Verification

1. **Counter parity** — each new census counter agrees between the projection and
   the filesystem fallback on a reindexed vault (the discipline in
   `tests/test_census.py` / `test_projection_counts.py`).
2. **Contract evaluation is pure** — `evaluate(contract, before, after)` performs
   no I/O and no mutation; property-tested over synthetic before/after pairs.
3. **Two-sided harness gate (§8)** — an unchanged vault PASSes; a synthetic
   after-state violating each INTENT bound and each ENVELOPE entry FAILs, one test
   per clause.
4. **Freeze guard** — verification against an uncommitted contract raises, mirroring
   `test_verify_harness.py`'s baseline guard.
5. **Loop termination** — the workflow restores and escalates at round 3 rather
   than looping or merging.
6. **Full suite green** — `ATELIER_EMBED=off python3 -m pytest -q` (731 at the time
   of writing).
