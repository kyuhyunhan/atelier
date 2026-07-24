# Changelog

All notable changes to atelier.

## [Unreleased]

### Added — RFC 0009 G0c-1: the goal orchestrator and the two-sided gate

The three layers wired together, and the test the whole program exists to pass.

`goal.py` — `verify_contract` scores INTENT, ENVELOPE, and the global invariants
against a (before, after) pair, and is where a `supersedes` release is actually
*applied* (the other modules only shape-check it). A pure core (no git, no vault)
plus an operational wrapper `verify_contract_run` that reads the committed
contract, checks the pins, and generates the after-state.

- **Invariants are decomposed into per-clause checks, driven by
  `schema/data/invariants.yaml`** (§3.3, hard rule #3). INV-4 guards two
  quantities, so it splits into `surfacing.visible / forbids-fall` and
  `surfacing.dark_count / forbids-rise`; a `supersedes` entry releases exactly
  the matching clause, and its sibling stays gated — the coarseness §3.3 warns
  against is now impossible. INV-1 (no node kind vanished) stays whole and
  unreleasable: a goal never legitimately reduces a node *kind*.
- **`vault.content_fingerprint`** (§5.7) enters the baseline: one aggregate hash
  over `(relpath, sha256(body))` for every non-derived vault markdown file
  (`INDEX.md`/`MEMORY.md` excluded, content only, never `mtime`). The envelope
  checks it for equality; a vault-mutating goal releases it through a waiver and
  bounds `vault.changed_paths.count`, which the orchestrator computes by diffing
  the per-file digest maps the round baseline carries under `_file_digests`.

**The §8.1 two-sided gate, end-to-end.** `test_goal_run.py` builds a real git
repo, commits a pinned contract, and drives `verify_contract_run` against the
live temp vault: an unchanged vault PASSes; a **real minted claim** FAILs, caught
through the actual census→metric→fingerprint path (not a synthetic after-dict, in
which a counter hard-wired to a constant would pass both sides — the vacuous PASS
the program exists to prevent); and a declared-and-waived reduction PASSes, so the
FAIL is the delta, not the harness refusing everything.

Not in this PR (→ G0c-2, the operator surface): the `goal` workflow, the
`0009-baseline.json` capture, and the CLI entry. 19 new tests; 786 → 797.

### Added — RFC 0009 G0b: the delta-contract evaluator and freeze guards

The core logic of the goal program, in two modules kept deliberately apart.

`contract.py` — the **pure** evaluator. Given a contract and a (before, after)
pair of baselines, it scores INTENT (did the declared change happen), ENVELOPE
(did anything else move), and shape-checks `supersedes`. No I/O, no clock, no
git — so it is exhaustively property-tested against synthetic dicts.

- **An unknown metric key raises, never resolves to zero.** `_leaf` returns a
  sentinel on absence rather than the `0.0` `_metric_not_regressed._get` returns
  today, so a typo'd `{"eq": 0}` clause cannot pass while proving nothing
  (§8.1.3). A malformed bound, a non-numeric target, or a non-default envelope
  mode all raise too — a contract that cannot be evaluated is a broken harness
  (a hard abort, §6), not a missed target (a FAIL).
- **ENVELOPE is default-deny over a union namespace.** The namespace is the
  numeric leaves under `metrics`/`census`/`surfacing`/`eval` (excluding
  `_`-prefixed and non-numeric leaves, §5.1.1) plus `vault.content_fingerprint`,
  taken over `keys(before) ∪ keys(after)`. A metric present on one side and
  absent from the other raises — so dropping a counter cannot dodge the envelope,
  the dodge default-deny closes one level down.
- **A waiver releases one metric and bounds another** (§3.5), because
  `vault.content_fingerprint` is a hash string that cannot carry a numeric bound
  — so a vault-mutating goal releases it and bounds `vault.changed_paths.count`
  instead. Review caught that the first draft's same-metric-only waiver could
  never express this, which would have forced a data-model rewrite at G5; the
  split lands now. A same-metric waiver omits `bound.metric`; a waiver on a
  metric outside the namespace (an inert typo) raises; one without a reason
  raises. **`supersedes` is per-clause and needs a matching INTENT bound**
  (§3.3): releasing an invariant the contract did not also bound is disabling a
  gate it never earned, and raises.
- **`from` is an integrity check, not decoration.** A clause declaring
  `from: 830` against a before-snapshot that reads 500 was authored against a
  different baseline, and raises rather than being graded.

`freeze.py` — the integrity guards, the only impure part. A contract is read from
its committed git blob (never the working tree), and pinned by content and
ancestry rather than cleanliness — the two weaknesses review found in the RFC
0006 baseline guard.

- **`captured_at_head` must be exactly the contract commit's first parent**, not
  merely some ancestor (§3.1.1) — that tightening removes the "some older commit
  the author picks" free variable. The round baseline's hash and any probe
  fixture's hash are pinned in the contract too, so rewriting the before-picture
  or the probe mid-run fails closed.
- A dirty or uncommitted contract raises rather than being graded as a draft;
  the pins all live *inside* the contract, the run's only git-pinned artifact,
  because a manifest under `~/.atelier/cache/` would be as writable as what it
  attests to.

Not in this PR (deferred to G0c, the end-to-end wiring): the `verify_contract`
orchestrator that combines these with the global invariants and *applies* a
supersession, the `goal` workflow, and the `0009-baseline.json` capture. 28 new
tests (a real throwaway git repo for the freeze guards); 755 → 783.

### Added — RFC 0009 G0a: the goal-program metrics

The five quantities a *deliberate reduction* can be gated on, in a new
`metrics` block of the baseline. They reproduce the RFC's §2 measurements
exactly — promote eligibility 830 (knowledge 807 / operational 23), pending
36 with a 38-day tail, zero active PII patterns, one lens-scoped surface of six.

- **`metrics` is a sibling of `census`, never inside it.** INV-1 reads `census`
  as a monotone "no node kind shrank" gate, so a counter a goal must drive
  *down* would become a gate against its own goal if it landed there. A test
  asserts the block is invisible to `_census_kind_totals`.
- **Counters wrap the production predicate rather than re-implementing it.**
  `promote_eligible` routes through `projection_counts` (already the thin
  wrapper over `claims_io.is_promote_eligible`) with the same filesystem
  fallback the feature uses — mandatory, because a cold DB answers `None` and
  under the abstain rule a `None` becomes key-absence and aborts a run. The
  divergence test asserts equality with `promote.propose._eligible`, and was
  verified to FAIL against the specific attack the RFC names: re-implement as
  `ac_status == 'passed'` and the born-accepted branch silently vanishes.
- **`pending_age` takes `as_of` as a required parameter.** It is the one
  wall-clock-derived metric; a counter reading the clock would give a different
  verdict tomorrow on identical commits.
- **`guard_liveness` counts active pattern lines**, and reports
  `pii_file_present` separately — so the state RFC 0008 left unspecified (a file
  that exists carrying only comments, reported healthy by every enforcement
  point while scanning nothing) is visible as the disagreement it is.
- **`lens_param_present` is named for what it can prove.** The denominator is
  schema data (`schema/data/lens_surfaces.yaml`, hard rule #3) so it cannot be
  shrunk to meet a bound, and the numerator is introspected from the live
  handler signature so the schema file cannot claim a parameter the code lacks.
  But a signature cannot show a lens is *honoured*, and review found the first
  draft both called it "coverage" and shipped a test asserting an
  accepts-then-ignores handler as the passing case — certifying the attack
  instead of disclosing it. The metric is renamed, the test now documents the
  blind spot, and the RFC records that G3 must add a behavioural gate (call a
  surface under two lenses, require the results to differ) before its `6/6`
  bound means anything.
- **`pending_age` abstains instead of reporting a zero tail.** Undated pending
  claims were dropped from the age list but still counted, so a queue of
  unparseable claims read `max: 0` — passing a `≤ 7` ceiling while the backlog
  rots, which is the exact defect `cross_project_noise` was withheld to avoid.
  Ages also clamp at zero, since verifying against a stale anchor otherwise
  takes a max over mixed-sign values.
- **`cross_project_noise` is deliberately absent** until its out-of-tree fixture
  lands. Under the abstain rule that absence is the honest signal — a `0.0`
  would pass a `≤ 0.15` ceiling and report green on a lens returning nothing.
- **Two abstain guards and a classification split**, all from the same review:
  `guard_liveness` no longer lets a non-UTF-8 or unreadable pattern file abort
  every other metric and every invariant (it is per-machine and user-managed);
  a malformed `captured_date` on an on-disk anchor no longer does either; and a
  surface declared in the schema with no handler now reports as
  `unimplemented` rather than as "lacks a lens", so a yaml typo cannot cap the
  count with nothing in the output to say why.
- `surfacing.audit()`/`snapshot()` gain a `vault` parameter. `baseline.generate`
  passed one to `eval.run` and `census.census` but could not to this, so a
  baseline taken against a temp vault silently read the live one for its
  surfacing block — which also made the RFC's end-to-end self-test
  unimplementable. `baseline.generate`/`write` gain `about`, since there is now
  more than one program anchor.

### Added — RFC 0009 (draft): goal-driven delta contracts

A design document only; no behaviour change. RFC 0006 P0 gave us a real
verification harness, but every gate in it is **monotone** — "did not shrink,
did not regress". The work that remains is subtractive (narrow a predicate,
narrow auto-pass, scope a surface), and a monotone gate meets a deliberate
reduction either by failing spuriously or — worse — by passing vacuously,
because the quantity that actually changed is not in the baseline at all.

- **Delta contract**: a goal declares its intended change *before* any code is
  written; the verifier scores INTENT (did it happen), ENVELOPE (did anything
  else move), INVARIANT (the never-break bar).
- **The freeze rule, strengthened rather than inherited.** Review established
  that the existing baseline guard is weaker than its docstring claimed:
  `atelier verify --allow-uncommitted` is a public CLI flag, and `git status`
  proves a file *clean*, not *old* — committing a widened bound defeats it.
  (RFC 0006 §6 specified "dirty **or** newer than the tag"; only the first half
  shipped. `verify.py`'s docstring is corrected here.) Contracts are pinned by
  blob sha and commit ancestry instead.
- **Freezing the *measurement*, not just the bound.** The counters ship in the
  same PR as the change they score, so a builder that cannot move a number can
  redefine it — reimplementing the promote counter without the born-accepted
  branch reports 23 on a vault that still proposes 830, and passes every clause.
  Counters are therefore thin wrappers over the production predicate, any
  denominator is schema data (hard rule #3), and the metric diff is part of what
  the critic accepts before implementation.
- **One narrow supersession path.** Three goals are *defined* by reducing what
  an invariant measures — auto-pass narrowing shrinks the accepted pool INV-4
  guards — so left alone the invariants make them unshippable through the
  harness built to ship them. A contract may name an invariant in `supersedes`
  only with a matching exact INTENT bound and critic acceptance; the new
  counters also land in a `metrics` block, never inside `census`, so INV-1 does
  not silently become a no-shrink gate on the quantity a goal must reduce.
- **A third snapshot class**: the per-run *round baseline*. The committed
  program anchor answers "have we drifted since the program began"; a single
  run's delta needs a *current* before-picture. The existing anchor is 19 days
  and +215 claims stale, which would bury a 23-claim intended delta in
  unrelated drift.
- **Five census counters** so the goals are observable at all — promote
  eligibility, pending *age* (the tail, not the count), guard liveness
  (**active** pattern lines, not file existence — the defect RFC 0008 M4
  shipped), cross-project noise, and lens surface coverage.
- **Convergence loop** with a critic gating the *contract*, a fixer that
  receives only failing checks, and abort-and-escalate at round 3 through both
  rollback mechanisms (git for code, snapshot for the vault — the snapshot
  restores the vault only, which the first draft conflated).
- **A two-sided harness gate whose failing side runs through the vault.**
  Injecting a fake delta into a synthetic dict exercises only the pure compare
  function, so a counter hard-wired to zero passes *both* sides. The gate now
  mints a throwaway claim and measures end-to-end, and contract evaluation
  raises on an unknown metric key rather than resolving it to `0.0` — the
  behaviour `_metric_not_regressed` has today, which would let a typo'd key
  satisfy `{"eq": 0}` while proving nothing.
- **PII posture for the new artifact classes** (hard rule #1): contracts carry
  counts and metric names only, and the cross-project probe fixture — which
  cannot be honest without naming real projects — lives out of tree under
  `~/.atelier/`, with the counter abstaining loudly when it is absent.
- **Every integrity root lives in the contract, the run's only git-pinned
  artifact.** A second review round found that the mechanisms added in the first
  had reintroduced the hole they closed: the round-baseline hash and contract
  blob sha were recorded in a manifest under `~/.atelier/cache/`, which the
  builder can rewrite alongside the artifact it attests to. The pins move into
  the contract, and the capture-time HEAD they now carry doubles as the missing
  proof that Snapshot preceded Implement.
- **The out-of-tree fixture is pinned by content**, since a blob sha cannot
  reach it — otherwise a builder rewrites the probe queries mid-run and the
  cross-project ratio falls with no lens change shipped.
- **Supersession is per-clause, and the invariant→metric map is schema data.**
  Without a declared mapping, "a supersedes entry with a matching INTENT bound"
  has no definition of *matching* — a contract could release the no-data-loss
  gate while bounding an unrelated metric. Per-invariant supersession was also
  too coarse: INV-4 gates two quantities, so releasing it for one silently stops
  gating the other. The recall invariants read the same accepted pool, so they
  are nameable too.
- **The envelope is default-deny.** An enumerated list makes "nothing else
  moved" a property of the contract author — the same problem one level up —
  and moving the counters out of `census` removed the incidental monotone floor
  they had been getting from INV-1.
- **A raise is a hard abort, not a FAIL.** A broken pin or an unknown metric key
  means the harness cannot be trusted for this run; retrying would let a builder
  convert a broken integrity check into three chances at a green one. A *third*
  round then found the raise over-applied: a lens change returning 18 rows
  instead of 20 would have escalated as an integrity failure, so the fixer that
  exists for "missed its target" would never run. Under-delivery is a FAIL;
  raises are reserved for a broken pin, an absent fixture, and a key no counter
  can emit.
- **The envelope's namespace is defined, and it is a union.** "Everything else
  unchanged" was asserted without ever saying what *else* ranges over — and the
  document contradicted itself, since one failure narrative used a `census`
  metric while the counters had been moved out of `census`. It is now the leaf
  keys of `metrics`, `census`, `surfacing`, `eval` plus the fingerprint, over
  `keys(before) ∪ keys(after)`: under intersection semantics, deleting a counter
  would drop it out of the envelope — the same dodge default-deny had just
  closed, one level down.
- **Waivers carry a bound.** A waiver defined as "a metric and a reason" is the
  enumerated envelope again, with an agent's judgement as the only thing between
  them. This was not theoretical: the fingerprint is a mandatory namespace
  member and INTENT bounds are numeric, so every vault-mutating goal must take
  the waiver path — the hatch was the normal path. A waiver is now a
  second-class INTENT clause, and a fingerprint waiver bounds changed-path count
  and prefixes, so "repaired 12 links" stays distinguishable from "rewrote 400
  files".
- **The ordering claim is withdrawn and the trust boundary stated instead.** The
  capture-time HEAD was described as making a late-captured baseline
  "structurally detectable"; it does not — git ancestry orders commits, not the
  work behind them, and the value is written by the graded party. The
  orchestrator is trusted to sequence the stages; the pins detect tampering
  *between* stages and prove nothing about their order. The pin survives as a
  consistency check tightened to the contract commit's first parent, which
  admits one value rather than any older commit the author picks.
- Records what is **not** goal-able: claim truth-decay ("migration COMPLETE")
  has no labelled set, so no honest bound can be stated. Inventing one would
  produce the vacuous PASS the whole RFC exists to prevent. The pending-review
  goal was nearly cut for the same reason — "a tool exists" is satisfiable
  while the 38-day tail rots — and survives only with a bound checkable against
  the counter.

### Added — RFC 0008 M3: depth (mint stays 1:1; deep atomize is additive)

The last milestone, and the one the measurement inverted. The draft would have
routed long memories away from the deterministic mint into LLM atomization —
but 88% of the real backlog is "long", so that routing would push nearly every
absorb into the expensive lane absorb exists to avoid, *and* discard the free
curated claim sitting in each memory's `description`. Length therefore never
changes the lane; depth is optional and human-directed.

- **Derived claims inherit the Source's sensitivity, never widen it**
  (`claims_io.atomize_write`). The `operational` domain default is public, so
  deep-atomizing a Source that M4 demoted to `private` (a `type: user` memory,
  or a PII-pattern hit) would have minted PUBLIC claims off its body — routing
  the content M4 narrowed straight back toward proactive push. Sensitivity now
  only ever TIGHTENS here, mirroring the dream-synthesis guard; abstain-on-miss
  when the Source cannot be resolved (lint L8 remains the audit backstop).
  `domain: personal` stays private regardless (Policy 1 unchanged).
- **The atomize skill scopes operational Sources explicitly** (out of tree):
  additive, on-request only, never on a backlog sweep — a minted Source already
  has a derived Claim, so it never enters the un-atomized count. "Shallow vs
  deep" is a judgement about whether a body has earned the tokens, not a
  derived state the engine can nudge on.
- Tests (7): a minted Source stays out of the atomize backlog (even at 400
  words), long and short memories both mint, deep atomization leaves the mint
  claim **byte-identical** while all claims share one Source, inheritance from
  a `type: user` Source and from a PII-demoted Source (claims **and** the
  entities minted alongside them), a public Source still yielding public claims
  (the guard tightens only), and `domain: personal` unaffected.
- **Lint L8 now audits what it claimed to.** Its predicate matched only a
  private *domain*, so a public claim derived from a `sensitivity: private`
  **operational** Source — exactly the abstain-on-miss case the write-time
  guard leaves to lint, plus any pre-M3 mint or a Source demoted after its
  claims existed — was invisible. It now flags a source that is private by
  domain (Policy 1) **or** by its own sensitivity (M4), and names which. L8 had
  no behavioural test at all (only a schema-wiring assertion); it has four now.
  Zero new findings on the live vault.
- **The claim→claim half is closed too.** `write_synthesized_claim` has carried
  the tighten-only guard since the dream cycle, but `principles.add` — the other
  claim→claim synthesis path, and the one that writes at `proactive`/`always` —
  did not: a principle generalized from private evidence landed **public** and
  was pushed every turn. Both now call ONE shared
  `claims_io.inherit_sensitivity_from_claims`, so a future synthesis path
  cannot ship with a subtly different (or absent) copy. Suite 717 → 731 green.

**RFC 0008 complete** — M1 discovery, M4 safety, M2 supersession, M3 depth, all
built, independently reviewed, and verified on the live vault. The sensitivity
invariant now has a write-time guard on every engine edge that derives a claim
(Source→claim: mint, atomize; claim→claim: dream, principles) plus an audit
half (lint L8) covering both the private-domain and private-sensitivity cases.

### Added — RFC 0008 M2: supersession (path-indexed ledger)

Closes the gap the M1/M4 hotfix could only *report*: an upstream memory edit
used to mint a second claim and leave the first live and stale, or (after the
re-mint guard) be dropped entirely.

- **Machine-independent path index.** The ledger gains
  `{by_sha, by_path}`; `by_path` keys are `<encoded-project-dir>/<filename>`,
  never an absolute `~/.claude/...` path — the ledger is git-tracked so dedup
  holds across machines, and an absolute key would never match elsewhere.
  A legacy flat ledger migrates in place on read, deriving `by_path` from each
  entry's own `source_path` (lossless, one-time). Entries without one simply
  cannot supersede — forward-only, the posture RFC 0007 took with the anchor.
- **Compute-then-branch, on the STATEMENT.** The claim id is
  `f(statement, source_id)` and the operational Source id is `f(statement)`
  alone, so the branch is decided before any write:
  - *body-only revision* (statement unchanged → same ids): the Claim file is
    **not written at all** — surfacing, `ac_status`, `accepted_at` and curated
    `links` survive — and only the Source's body is refreshed in place
    (`body_sha` updated, `revised_at` stamped, `entry_id`/`created_at` kept),
    via the new `claims_io.refresh_operational_source_body`.
  - *statement revision*: mint normally, carry the `refines → old_claim_id`
    edge on the **new** claim, and retract the old one through
    `ac_status: retracted` (`set_ac_status`) so it exits promote eligibility
    through the same field every other retraction uses.
- **Shared-description guard.** Two memories sharing one `description` collapse
  onto one content-addressed claim; if another live path still resolves to it,
  the retract is skipped (and warned) while the link is still recorded.
- **A fourth case the RFC did not anticipate, found by the tests.** Dedup
  hashes the BODY (frontmatter excluded), so re-titling a memory without
  touching its content arrives with an *unchanged* hash — yet the statement IS
  the description, so the claim id moves. Left alone it would strand the old
  claim exactly as an un-superseded body edit would. absorb now compares the id
  the statement resolves to against the one the ledger recorded (one uuid5 on
  the dedup path) and supersedes when they differ.
- absorb records report which branch a memory took: `body_refreshed`,
  `supersedes`/`superseded`, or `revision_dropped` (a *different* memory
  already owns this exact statement, so this body is stored nowhere).
- Tests: 16 new covering the machine-independent key, ledger migration
  (including idempotency and `source_path`-less entries), a promoted claim left
  **byte-identical** across a body revision, Source refresh without forking,
  `by_path` advancement, retract + `refines`, promote-eligibility actually
  lost, the shared-description guard, description-only supersession, and
  re-runs staying no-ops.
- **Review round 1** — five fixes, three proven against the live ledger:
  - *Migration crowned the wrong sha.* `_save_ledger` writes `sort_keys=True`,
    so a legacy ledger is sha-lexicographic, not append-ordered; "last one
    wins" picked an arbitrary entry. On the real ledger one path carries two
    shas and the older (2026-05-28) was winning over the newer (2026-07-22) —
    latent only because legacy entries lack `claim_id`, but the RFC's planned
    backfill would have armed supersession against the stale one and retracted
    the wrong claim. Winner is now max `absorbed_at`.
  - *A→B→A left every claim retracted.* Reverting a description re-mints onto
    the claim supersession had retracted; the re-mint guard (correctly) will
    not rewrite it, so the live memory owned nothing but retracted claims.
    A retraction **this mechanism authored** (identified by its
    `archive_reason`) is now reversed to `pending`; a curator's retraction
    never is.
  - *The shared-description guard was defeated by legacy co-owners.* It read
    the ledger, where pre-M2 entries carry no `claim_id` — with all 62 live
    entries in that state it would have retracted claims another live memory
    still mints to. Ownership is now computed from the **live upstream
    corpus**, which is exact and vintage-independent.
  - *`claim_path_for` was an O(vault) rglob+parse* — 7.7s on 6.5k claims, the
    same per-item-O(vault) smell the 243s nudge regression just cost. The
    writer's filename is deterministic, so it now probes the collision chain
    and verifies the stored id: measured **7.6ms**. The ledger records each
    absorb's `statement` to make that O(1) lookup possible.
  - *A body-only revision whose Source was deleted out of band* advanced the
    ledger while storing the revision nowhere; the Source is now re-created.
  - *Authorship was matched on prose.* The reviewer proved a curator
    retraction reading "superseded by my own better learning…" was silently
    reversed. Authorship is now a STRUCTURAL field (`retracted_by:
    absorb-supersession`), and `set_ac_status` — the single retraction
    gateway — clears it on every transition, so a stale marker can never
    outlive the retraction it described.
  Suite 695 → 717 green.

### Fixed — absorb hotfix: project-slug divergence + re-mint lifecycle clobber

Two defects found by a whole-system evaluation of absorb after its first live
run (25 memories). Both pre-date RFC 0008 and were invisible to diff-scoped
review because neither function was in any recent diff.

- **Project slug was written under a key recall could never match.** Claude
  Code encodes a working dir by replacing `/` with `-`, but a directory name
  may itself contain `-` — the encoding is **not injective**, so
  `decode_cwd_dirname`'s string split turned `inheaden/identity-hub` into
  `hub`, `inheaden/app/frontend` into `frontend`, `prayer-team` into `team`.
  Recall matches `project_hint` exactly, so absorbed claims could never receive
  the project boost, and bare-noun Concept entities (`hub`, `frontend`) were
  accreting in the graph. Now: the decoder **probes the real filesystem**
  (longest-component-first) to resolve the ambiguity, falling back to the naive
  split only when the path is gone; and `derive_project` routes through
  `project.resolve_project` — the single accessor capture/bootstrap/recall
  already share (its module docstring describes precisely this divergence).
  Verified against all live projects: 7/7 now agree with the session slug.
- **Re-mint silently clobbered lifecycle state.** `entry_id = f(statement,
  derived_from)`, so a re-capture/re-absorb of the same statement lands on the
  existing claim's path with freshly built BIRTH defaults. The unconditional
  write demoted `surfacing: proactive` → `query`, reset `ac_status`
  (un-retracting a curator-retracted claim), and wiped curated `links` — a live
  data-loss path with 98 absorbed claims already promoted. `write_operational_
  claim` is now idempotent on an existing same-`entry_id` file (mirroring
  `write_operational_source`) and reports `existed` in its result.
- **Slug resolution must not be paid per file.** `resolve_project` gained
  `need_known=False`: the slug layers are config/filesystem lookups, but the
  `known` probe is a DB query that falls back to scanning EVERY accepted node
  when a project has no learnings yet. Routing absorb through the resolver
  without this turned the session-start nudge count from milliseconds into
  **243 seconds** on the live vault (61 files × ~5s). Now: `derive_project`
  skips the unused `known` probe and is memoized per encoded directory, and
  `unabsorbed_count` reads memories with `with_project=False` (it needs only
  body hashes). Measured back down to **0.025s**.
- **An unverified decode no longer borrows a live project's identity.** When
  the project directory is gone, the naive fallback path (`…/app/fe` for a
  deleted `app-fe`) would hit the config map's *prefix* matching and be keyed
  onto a different, real project — contaminating that project's recall boost.
  Unverified decodes now fall back to the plain basename: a wrong-but-orphan
  key is safer than a wrong-but-real one.
- **The M2 gap is now surfaced, not silent.** With both writes idempotent, an
  upstream body edit that keeps its description is stored nowhere (the ledger
  records the new hash regardless). absorb now logs `absorb.revision-dropped`
  and marks the record `revision_dropped: true`; `capture` reports the claim's
  LIVE surfacing/ac_status (plus `already_captured`) instead of asserting birth
  defaults it did not write; `principles.add` warns on a no-op instead of
  reporting a success that changed nothing.
- Tests: hyphenated-directory fixtures (the original fixtures used
  non-hyphenated names — `lexio`, later a generic `project` — which is exactly
  why the defect survived), config-project-map routing, session-resolver
  agreement, decode stability under an ambiguous tie, unverified-decode
  isolation, memoization, a guard that the nudge count never resolves projects,
  the revision-dropped signal, and lifecycle-preservation pins for
  promote/retract re-mint. Suite 676 → 688 green.
- **The decoder fix is forward-only** — it corrects what absorb writes from now
  on, not what is already on disk. The corpus repair ships as the migration
  below.

### Fixed — vault migration: absorbed claims repaired to the resolver's slug

- `scripts/migrate_absorbed_project_slugs` (one-time, dry-run by default)
  repairs claims already written under a mangled slug. Re-absorbing cannot do
  this: dedup is by `body_sha` and both writes are idempotent, so a re-run
  lands on the same files; delete-and-re-mint would rewrite them at the cost of
  the lifecycle state the guard above exists to protect. Markdown is truth, so
  the repair is an in-place frontmatter edit.
- Ran on the live vault: **14 claims** corrected (`frontend` →
  `inheaden-app-frontend`, `hub` → `inheaden-identity-hub`, `mobile` →
  `inheaden-app-mobile`), `is_about` repointed from the bare-noun `Concept`
  entities to correctly-labelled ones, and **3** now-unreferenced bare-noun
  entities retired. `entry_id` is deliberately untouched — it is
  `f(statement, derived_from)` and neither input changes — so links,
  `derived_from` edges and dedup all stay valid.
- The script **refuses to guess**: a claim whose project directory is absent on
  this machine is skipped and reported, never rewritten. Without that guard a
  re-run elsewhere would invert the migration — `derive_project` falls back to
  the naive basename precisely when it cannot verify, and that fallback *is*
  the mangled slug. Entity lookup is keyed on (type, label, scheme), and
  retirement scans entity→entity `links` as well as claims' `is_about`.
- The dedup ledger (`.absorbed-from-claude.json`) is deliberately left alone:
  membership is by `body_sha` only and its `project` field is informational, so
  rewriting it would risk the dedup key for no gain.
- Verified after reindex: 0 slug mismatches, 0 dangling `is_about` refs, 6/6
  projects agree with `resolve_project`, doctor v7-green; a second run is a
  clean no-op.
- Tests (7): repair + entity retirement, dry-run writes nothing, second apply
  is a no-op, absent-project-dir skipped, **the inversion case** (an already-
  repaired claim on a machine missing the directory is not re-mangled),
  pre-mint claims without `source_path` untouched, and an entity still linked
  from another entity is not retired, and the dry run announces the entity it
  would retire (the first preview compared Paths by object identity — `rglob`
  returns fresh objects, so it silently reported "none" while `--apply`
  unlinked). Suite 688 → 697 green.

### Added — RFC 0008 M1+M4: absorb nudge + safety at the absorb boundary

- **M1 discovery** (`absorb_claude.unabsorbed_count` / `nudge_info`): a fourth
  nudge kind `absorb` — a memory is *unabsorbed* iff its normalized body sha256
  is not in the vault dedup ledger (deterministic, read-only, LLM-free). Both
  absorb and the count go through one ledger accessor (`_is_absorbed`) so M2's
  `by_sha` nesting changes one function. Surfaced in the unified nudge list
  (`absorb → atomize → promote → dream`) and at session bootstrap
  (`absorb_nudge` key + markdown line). Threshold:
  `learnings.absorb.nudge_after_memories` (default 1). Human-pulled, never cron.
- **M4 safety** (demote-never-block): a `type: user` memory (who the user *is*)
  now lands `sensitivity: private` on Claim AND Source; a body/statement match
  against `~/.atelier/pii_patterns.txt` (the same pattern file the pre-commit
  guard reads) demotes to private and stamps `pii_flag: true` on both nodes.
  Missing pattern file → no-op. The promote gate requires public, so demoted
  claims can never be proactively pushed.
- Tests: 15 new (count/threshold/nudge shapes, bootstrap surfacing, sensitivity
  defaults, PII demotion, rerun dedup); conftest isolates the two
  outside-the-vault reads (`~/.claude` memories, PII patterns) into the temp
  workspace. Full suite 673 green.

### Added — RFC 0008 (draft): absorb lifecycle — discovery, supersession, depth, safety

- Design RFC (`docs/rfc/0008-absorb-lifecycle.md`) for the perimeter around the
  RFC 0007 absorb path (`~/.claude/projects/*/memory/` → vault), grounded in a
  live census (25 unabsorbed memories; median body 253 words — the "one file,
  one fact" premise does not hold, which reverses the obvious depth design).
- **M1 discovery**: a fourth nudge kind `absorb` (unabsorbed = body-sha not in
  ledger); human-pulled, never cron. **M4 safety**: `type: user` memories land
  `sensitivity: private`; a PII pattern pass (same file as the pre-commit guard)
  demotes to private + flags, never blocks. **M2 supersession**: path-indexed
  ledger (machine-independent keys); compute-then-branch on statement change —
  body-only revisions refresh the Source body in place (Claim file untouched),
  real supersessions retract via `ac_status: retracted` + a `refines` link on
  the new claim. **M3 depth**: mint stays 1:1 (statement = the curated
  description); deep atomize is additive + human-directed, inheriting the
  Source's sensitivity (tighten-only). Sequencing M1 → M4 → M2 → M3.

### Changed — clearer nudge wording (atomize domain-split, promote cap indicator)

- **Atomize nudge** now splits the un-atomized backlog by the human-gate
  (`atomize.unatomized_by_gate`): a private-domain (`personal`) Source is
  surfaced as **human-gated** ("atomize only when you direct it, not a blind
  pass"), while other domains say "run `atelier-atomize`". Fixes the confusing
  case where a lone personal diary read as "1 un-atomized source, run
  atelier-atomize" — which the skill (correctly) refuses to auto-run on personal
  content (Policy 1: personal is atomizable but human-gated, claims stay
  `sensitivity: private`).
- **Promote nudge** shows `N+` when the eligible count hits the scan cap (50),
  so the number no longer reads as an exact total when the backlog is larger; and
  clarifies "accepted" as "passed review, or atomize-born knowledge".
- Tests: domain-split + personal-only nudge messages; updated existing wording
  assertions. Full suite 658 green.

### Changed — RFC 0007 M3: freeze the shared anchor (principles.py)

- `principles.py` no longer creates the shared `operational-capture` anchor. It
  was the last production writer to touch it, and for evidence-bearing synthesis
  it created the anchor then overrode `derived_from` — leaving an **orphaned**
  anchor Source that the atomize nudge would flag as un-atomized forever (a
  latent leak the M2 review surfaced; deployed vaults were masked by legacy
  anchor-hung claims). Now: evidence-bearing principles use the anchor id only as
  an id-stable discriminator string (no file created; `derived_from` points at
  the evidence), and evidence-less principles are born from their **own**
  content-addressed operational Source — same born-as-Source model as
  capture/absorb. The anchor is now fully frozen: no writer creates or attaches
  new claims to it; existing anchor-hung claims are grandfathered.
- `claims_io.ensure_operational_source` (the create-once anchor **writer**) is
  removed — it had zero callers after M2/M3, and a dead anchor-writer is a loaded
  gun that would re-arm the orphaned-Source class the freeze eliminates.
  `operational_source_id()` (the id-stable discriminator string) is retained.
- Guard tests (split so the count assertion is a genuine 1→0 guard): an
  evidence-bearing principle leaves no orphaned anchor
  (`atomize.unatomized_count == 0`, no anchor file); an evidence-less principle
  is born from its own `raw/operational/` Source (not the anchor id). Full suite
  656 green.
- (`raw/knowledge/_new/` removal is a vault-side cleanup, handled separately in
  the gorae content repo — not an engine change.)

### Changed — RFC 0007 M2: capture/absorb wired to the mint path (live behavior)

- `capture()` (`runtime/service/learnings/capture.py`) and `absorb()`
  (`runtime/service/learnings/absorb_claude.py`) now call
  `mint_operational_claim` instead of `ensure_operational_source` +
  `write_operational_claim`. Each operational learning is born as its **own**
  content-addressed Source in `raw/operational/` (not the shared
  `operational-capture.md` anchor), and the Claim is deterministically minted
  (`generated_by: mint`, was `ingest`). Session provenance is mirrored onto both
  the Source and the Claim; `is_about` entities now scheme as `operational`.
  absorb carries `body_sha` / `source_path` / `claude_memory_type` on the Source;
  its body-hash ledger still guards re-import.
- `raw/operational/` added to the **dev lens** (`schema/data/lenses.yaml`) — the
  raw form of the primary coding-session content.
- `capture`/`absorb` no longer attach to the shared anchor; existing
  anchor-hung claims are grandfathered. (`principles.py` still writes the anchor
  for evidence-less synthesis — freezing that writer and removing the vestigial
  `_new/` dir is M3.) Tests updated from the old shared-anchor/`ingest` behavior
  to the per-item/`mint` behavior; new coverage for the acceptance-criteria
  mirror at the `capture()` level and for absorb's per-memory Source. Full suite
  654 green.

### Added — RFC 0007 M1: mint primitives (additive; writers unwired)

- Schema (additive, non-breaking): `operational` added to the `source.domain`
  and `entity.in_scheme` enums; `mint` added to the claim `generated_by` enum
  (`schema/data/graph.overlay.yaml`). A dedicated **content-only** Source id
  template `operational: "atelier:operational:{content_hash}"` (no `created_at`)
  and an `operational` intake lane (`raw/operational/`) in
  `schema/data/structure.yaml`; `resolver.operational_source_dir()` derives it.
- Engine (`runtime/service/learnings/claims_io.py`): `write_operational_source`
  (create-once, content-addressed by `sha256(norm(statement))`) +
  `mint_operational_claim` (deterministic, LLM-free 1:1 mint with
  `generated_by: mint`, mirroring `session_id`/`working_dir`/`project_hint` onto
  the Claim for the promotion acceptance criteria). **Not yet called by
  capture/absorb** — the live write path is unchanged (that wiring is M2).
- Tests (`tests/test_operational_mint.py`): content-addressed id is a pure
  function of the statement; same lesson dedups to one Source + one Claim
  (the property that replaces the shared anchor); acceptance-criteria field
  mirror; additive-enum validation end-to-end.

### Added — RFC 0007 (draft): single intake front door — born-as-Source + deterministic mint

- Design RFC (`docs/rfc/0007-single-intake-front-door.md`) revising RFC 0005
  §7.1. Today operational learnings (`capture` + `absorb`) are *born directly as
  Claims* on one shared anchor Source (`raw/inbox/operational-capture.md`, P10) —
  a second intake track that bypasses `raw/` and flattens provenance (every
  operational Claim shares one `derived_from`). This is the root of the recurring
  "domain-blind pipeline stage" bug class (a stage written against the track its
  author saw first forgets the other).
- Proposal: every input lands as **its own Source** in `raw/`; the `raw → Claim`
  edge splits by domain lane into **deterministic mint** (LLM-free 1:1, for the
  new content-addressed `raw/operational/` lane) vs **generative atomize** (LLM,
  for raw material). Anchor is **frozen to legacy** (forward-only migration — no
  legacy-claim rewrite, since `claim_id = f(statement, derived_from)` makes id
  conservation-under-repoint a contradiction). Content-addressed operational
  Source id (`atelier:operational:{content_hash}`, no `created_at`) preserves
  `capture`'s ledger-less cross-session dedup.
- Adversarially reviewed twice (independent subagent): first pass caught the
  entry_id/`derived_from` coupling that made the original migration self-
  contradictory; second pass confirmed the forward-only + content-addressed
  rewrite is sound and caught the acceptance-criteria mirror requirement. Status:
  Draft (awaiting implementation gates M1–M4).

### Fixed — dream cadence tracks the proactive pool, not accepted-operational

- The dream nudge's accumulation trigger counted *accepted operational
  learnings* since the last dream — a proxy from when the only path to the
  proactive tier was the operational accept→promote flow. Dream actually
  clusters the **proactive pool** (any domain, no gate — `cluster.load_proactive_
  claims`), so after the promote gate was made domain-aware, knowledge could
  reach proactive but never moved the dream nudge: the cadence was blind to it
  (the same domain-blindness, one pipeline stage downstream of promote).
- `dream_status` now counts `proactive_since_last_dream` (via the new
  `projection_counts.proactive_count` + `cluster._count_proactive`, filesystem-
  truth with projection fast-path), and `mark_dream_complete` baselines the
  proactive count. `nudge_info` reads a new config key `nudge_after_proactive`
  (falls back to `nudge_after_accepted` for compat). Net effect: promoting
  knowledge query→proactive now grows the dream signal, so the whole
  atomize→promote→dream pipeline is domain-aware end to end. The standalone
  accepted-learnings count (`accepted_operational` / `_count_accepted`) is kept
  for learning stats but no longer drives dream.

### Fixed — promote eligibility is now domain-aware (knowledge is promotable)

- The query→proactive promote gate required `ac_status: passed`, a field only
  operational learnings carry (their human accept-gate). Atomize-born knowledge
  claims have no `ac_status`, so every knowledge claim was permanently locked at
  the query tier — never promote-eligible, so never dream-visible either, and
  the promote nudge stayed silent no matter how much knowledge was atomized
  (the same class of gap as the atomize nudge only counting `kind: source`).
- New single predicate `claims_io.is_promote_eligible(fm)` — shared by the
  filesystem scan (`promote.propose._eligible`) and the DB projection
  (`projection_counts.promote_eligible`) so they can't drift — encodes a
  domain-aware gate: on the query tier, `sensitivity: public`, and past
  acceptance where **operational needs `ac_status: passed` but an absent
  `ac_status` (atomize-born knowledge) counts as accepted** (atomization is its
  curation). Private claims are never eligible (never pushed proactively). The
  raw-source and claim schemas are unchanged — this fixes the gate, not the data.

### Added — deterministic atomization write-path (`atomize_write`)

- `claims_io.atomize_write(source_entry_id, created_at, domain, entities, claims)`
  and the `atelier_atomize_write` MCP tool: the engine now owns the mechanical
  write for turning a raw Source into v7 graph nodes. The agent supplies only
  judgement — structured `{entities:[{type,pref_label}], claims:[{statement,
  attributed_to,is_about:[pref_label…]}]}` — and the engine resolve-or-creates
  the *typed* entities (`_resolve_typed_entity`; the type is part of the
  content-addressed id, so an AI model filed as `Model` never collides with a
  same-named `Concept`), resolves each claim's `is_about` labels to entity ids
  (auto-minting a `Concept` for any undeclared label so `is_about` never
  dangles), and mints content-addressed, deduped, hashed claim nodes. No LLM,
  no per-source script. Idempotent by content-addressing.
  This closes the gap that made atomization expensive: the skill/agents were
  hand-authoring a resolver+write script per source (the token sink), when that
  work is deterministic and belongs in the engine — "judgement is the LLM's,
  the write is the engine's."

### Fixed — YouTube ingest recovery (external breakage since ~2026-06)

- `atelier_youtube` stopped working against current YouTube for two external
  reasons, neither an atelier regression: (1) YouTube tightened bot detection
  and now intermittently challenges unauthenticated requests ("Sign in to
  confirm you're not a bot"); (2) recent player-API changes make yt-dlp's `-J`
  fail on format resolution even when only metadata is wanted. Fixes:
  - `_fetch_metadata` now always passes `--ignore-no-formats-error` (metadata
    dump no longer aborts on the format-resolution error) and, when
    `youtube.cookies_from_browser` is set, `--cookies-from-browser <b>` to clear
    the bot wall. The browser is config-driven (`YouTubeConfig`), never
    hard-coded, so a distributed adopter picks their own without a source edit.
  - `_vtt_to_markdown` now cleans YouTube auto-caption noise it previously
    passed through verbatim: strips inline word-timing tags (`<00:00:06><c>…`)
    and collapses the rolling-caption duplication (each cue re-emits the prior
    cue's tail) via token-level suffix/prefix overlap. The collapse runs **only
    when inline word-timing tags are present** (i.e. ASR captions); manual
    subtitles carry no tags and are emitted verbatim, so a coincidental
    boundary-word repeat on the human track is never silently dropped.
    `word_count` is now computed from the body instead of hard-coded `0`.

### Fixed — encode-pipeline drift: descriptions caught up to retired behavior

- `atelier_promote_propose`/`atelier_promote_apply` MCP descriptions (and the
  promote/ section of ARCHITECTURE.md's module map) claimed the retired
  workshop→wiki page-writing flow; promotion has been a `surfacing:
  query→proactive` field transition in place since RFC 0005 §7.1. The
  `atelier_youtube` description's `provenance/knowledge/` is now
  `raw/knowledge/` (the real destination).
- `gorae.overlay.yaml`: the pre-v7 `digest`/`theme`/`synthesis` page types are
  now explicitly marked RETIRED (legacy-compat only) instead of silently
  outliving their writers — definitions stay so legacy vault pages keep
  validating, but the comment forbids new pages of these types.

### Added — docs/USING.md: the three-verb daily contract

- The ~19-subcommand CLI surface is now explicitly split into a
  **user contract** (three verbs: write / ask / tend, plus four escape
  hatches) and **engine/maintainer surface** (everything else, invoked by
  hooks and the daemon). `docs/USING.md` is the single page a daily user
  needs; `docs/ADOPTING.md` stays the install-time recipe.

### Added — always-on serve (launchd) with resource guardrails

- `atelier daemon {install,uninstall,status}` — serve becomes a login-started,
  crash-restarted launchd agent, ending the "engine silently off → every
  automation becomes a manual chore" failure mode (the root cause of a month of
  stale reindex and two months of stalled encoding). The guardrails are SPEC,
  not prose (the statusline CPU-melt lesson):
  G1 single instance (existing pidfile) · G2 no crash-loop spin
  (ThrottleInterval 60) · G3 low priority (ProcessType Background + Nice 10) ·
  G4 work-only-on-work (existing quiescence gates; zero LLM in-engine) ·
  G5 embed cap — the autosync piggyback reindex skips the embedding pass when
  a commit changed more than `auto_commit.embed_max_changed` (default 50)
  files, deferring bulk-edit vectors to a manual reindex.
  Kill switch: `atelier daemon uninstall`. Visibility: `atelier daemon status`.

### Changed — session-anchored daemon is now the default; launchd demoted to opt-in

- Root cause found (live reproduction, not guessed): a process launchd spawns
  does **not** inherit the interactive user's macOS TCC grants, so
  `atelier daemon install` silently fails to read a vault under
  `~/Documents`/`~/Desktop`/`~/Downloads` — the exact same `git` command that
  works from a Terminal shell fails with `Operation not permitted` when run
  under `launchctl submit`. Fixing this by asking users to grant Full Disk
  Access via System Settings is a manual, per-machine, GUI-only step —
  unacceptable as the *default* for a project meant to be distributed to
  machines its author never touches.
- New default: `atelier daemon {ensure,stop}`. A Claude Code `SessionStart`
  hook calls `atelier daemon ensure` on every session; it spawns
  `serve --http` detached iff nothing already holds the existing pidfile
  (G1 — idempotent, near-instant when already running). Because it runs as a
  child of the interactive session's process tree, the spawned serve
  **inherits the caller's TCC grants** — zero manual permission steps on any
  machine. G2 (no crash-loop spin) becomes structural rather than a throttle:
  there is no auto-restart-on-crash at all, so a crash loop is impossible by
  construction; serve only restarts when a new session starts. G3 (low
  priority) moves from the plist to `os.nice(10)` in the spawn itself.
  `atelier daemon stop` is the manual kill switch (SIGTERM via the pidfile).
- G1's pidfile guard (`server._acquire_pidfile`) is now an atomic
  `flock(LOCK_EX|LOCK_NB)`, not a read-check-write on the file's contents.
  Session-anchoring introduces genuinely concurrent callers — several Claude
  Code sessions can each background `atelier daemon ensure` at once — and the
  old exists()→read→write sequence was a TOCTOU race under that concurrency
  (two `serve` processes could both pass the liveness check before either
  wrote, one hitting a raw port-bind crash instead of a clean
  `AlreadyRunning`). The kernel now arbitrates: exactly one caller wins the
  lock, every other gets `AlreadyRunning` / exit code 3. The pidfile is no
  longer unlinked on release (only unlocked) — flock is per-inode, not
  per-path, so unlinking would reopen a window for a third process to
  create-and-lock a fresh inode at the same path while a racing acquirer
  still held the old one; staleness is decided purely by lock availability.
  Because content is now permanently stale, `daemon._pidfile_state()` (used
  by `ensure`/`status`/`stop`) was changed to ask the same lock
  (`server.is_locked()`) rather than reading the pid back and `kill(pid,
  0)`-checking it — otherwise a reused pid would look "running" forever
  after a clean stop.
- `atelier daemon {install,uninstall,status}` (launchd) is **kept** as an
  opt-in/advanced path for machines that need serve alive with no Claude Code
  session running (e.g. headless automation) — `install` now warns when the
  configured vault sits under a TCC-protected folder. `status` reports serve
  liveness for **either** mechanism.
- `vault_autosync`'s startup diagnostic no longer reports the misleading
  "vault is not a git repo root" when the real cause is a TCC permission
  denial — it now names the cause and points at `atelier daemon ensure`.
- `config/example.config.yaml` recommends a vault path outside
  `~/Documents`/`~/Desktop`/`~/Downloads` (TCC, and iCloud/git version races).

### Added — human/machine commit separation in vault autosync

- `vault.auto_commit.split_human_commits` (default **true**): the autosync
  poller now lands `raw/` (the human tree) and the engine tree (`graph/`,
  `workshop/`, manifests) as **separate, path-scoped commits** — `journal:` vs
  `message_prefix` — instead of one fused `add -A`. The diary's git history
  stays human; the machine's extractions are reviewable in isolation (clean
  PII-review surface). Same repo, same durability, quiescence gate unchanged.
  New primitive `github.commit_split` (each commit's message lists its own
  staged paths); threaded through `orchestrator.commit_push`
  (`split_human_tree`, human tree = `content_root()` from the structure
  resolver — hard rule #3). `false` restores the legacy single commit.

### Added — the personal invariant, enforced (Policy 1)

- **Policy decision (2026-07), resolving a live contradiction**: ARCHITECTURE.md
  said personal is "NEVER atomized" while RFC 0005 §7.2 permits it under the
  human gate — and the live corpus had already atomized 598/600 personal
  sources into 3,433 claims (80.5% of the graph), every one of them
  `sensitivity: private`. The invariant that actually holds (and now enforced):
  **personal may be atomized under the human gate, but derived claims must
  never LEAK** — `sensitivity: private` keeps them behind the recall
  sensitivity_gate and outside the dev lens (RFC 0006 ③).
- `schema/data/structure.yaml` `atomize.private_source_domains` (rule as DATA,
  hard rule #3) + resolver accessor.
- **Lint L8** (`private-domain-claim-leak`, severity FAIL): a claim
  `derived_from` a private-domain source must be `sensitivity: private`. Ships
  green on the live vault (0 violations / 3,433 personal claims). This is the
  only layer that catches direct agent markdown writes (the atomize skill
  bypasses engine APIs) and sources re-domained to personal after the fact.
- **Dream guard** in `claims_io.write_synthesized_claim`: a synthesis with any
  private-domain or private-sensitivity upstream inherits `private` (sensitivity
  only escalates) — closing the one engine path that could launder personal
  content into an always-surfaced (T0) principle. Abstain-on-miss; L8 is the
  audit backstop.
- ARCHITECTURE.md prose corrected to the decided policy.

### Added — Pillar ④ Curated: flag-only forgetting (RFC 0006 P4)

- `lateral.plan_forgets()` — flags accepted learnings the surfacing audit
  reports DARK as retraction *candidates*, mirroring `plan_merges`'s flag-only
  governance: dark does not imply forget, a human calls `review.retract(slug=)`.
  Reuses the SAME `surfacing.audit` the omission gate (INV-4) trusts, so a
  flagged candidate is provably unreachable by the identical measure the
  verifier uses — not a second, drifting definition of "forgettable." Folded
  into `atelier_lateral_plan`'s response (`forgets` key).
- ④b (hybrid retrieval) was already live (RFC 0002); this pillar adds no new
  retrieval code, only the `P4_curated` rubric's regression guard (reuses the
  existing `paraphrase_recall` invariant).
- `verify.py` gains `P4_curated` (`forgets_flag_only` gate: the plan call must
  never mutate the accepted pool). Live verify: 0/167 dark candidates on the
  live vault (consistent with the healthy P0.2b state), pool unchanged.

**RFC 0006 program complete** — all four pillars (Grounded, Fresh, Scoped,
Curated) built, independently reviewed, and verified against the frozen
baseline on the live vault.

### Added — Pillar ③ Scoped: serving lenses at the recall boundary (RFC 0006 P3)

- `atelier_learning_recall` now takes a `lens` (default **`dev`**): a coding
  session's per-turn recall excludes personal-domain claims (~80% of the graph),
  keeping operational + knowledge. Pass `lens='full'` for the cross-domain,
  wall-less view. Threaded through `recall_v7.recall_claims` → `rank_claims`,
  which filters the scored hits by the `(kind, domain)` lens map from ①
  **before** the top-k cut (so the budget fills from the admitted set).
- `lenses.lens_admits_fm` dispatches claim/source on scalar `domain` and entity
  on `in_scheme` (all-match). `verify.py` gains the `P3_scoped` rubric
  (`dev_lens_no_personal` gate). Live verify: dev lens clean over 100 recalled
  claims, no regression to operational recall.

### Added — Pillar ② Fresh: per-file change feed + indexed columns (RFC 0006 P2)

- `reindex.reindex_path(cfg, path)` + `api.reindex_path(path)` — project ONE
  file into the DB without a full reindex, reusing the exact reindex passes
  (parse → classify → upsert → chunks → links → prune), embed-skipped by default
  for speed. Parity with a full reindex is tested (incremental page == full).
- `schema/db/sql/0004_routing_columns.sql` — indexed VIRTUAL generated columns
  `kind`/`domain`/`ac_status`/`surfacing` on `pages` (+ indexes), so the lens
  (③) filters without a JSON scan. VIRTUAL because SQLite can't ALTER-ADD a
  STORED generated column; picked up on `rm cache && reindex` (rebuildable
  projection). Correctness never depends on them — readers may still json_extract.
- `verify.py` gains the `P2_fresh` rubric (invariants; structural checks in the
  suite). **Deliberately NOT auto-wired:** eager write-through on capture shifts
  dream-cadence + cold-DB-fallback semantics (12 tests proved the ripple), so
  reindex_path is an opt-in change-feed mechanism; adopting it on write paths is
  a scoped follow-up.

### Added — Pillar ① Grounded: lens vocabulary + vault manifest (RFC 0006 P1)

- `schema/data/lenses.yaml` — the serving-lens vocabulary (data, not code),
  keyed on `(kind, domain)`: `dev` (operational + knowledge, **personal
  excluded**), `life` (personal + knowledge), `full` (everything; the no-wall
  lens). Read via `runtime/structure/lenses.py` (`matches`, `validate_coverage`).
  Grounds Pillar ③'s scoping: personal is ~80% of claims (3433/4262), so the dev
  lens is the lever that removes coding-session noise.
- `runtime/structure/manifest.py` — `.atelier-vault.yaml` (structure_version +
  stable vault_id); `atelier setup` now grounds the vault idempotently, ending
  the "infer the era from which dirs exist" archaeology.
- `verify.py` gains the `P1_grounded` rubric (lens-coverage + manifest gates on
  top of the invariants). Verified on the live vault: all gates green, no
  regression.

### Added — independent verifier + workflow template (RFC 0006 P0.3)

- `verify.py` (`verify_against`) recomputes the after-state and scores it against
  the frozen baseline under a rubric; global invariants (INV-1 no-data-loss,
  INV-4 no-omission-regression, self-probe/paraphrase no-regression) always
  apply, pillar rubrics extend them. Guards: refuses a non-frozen baseline
  (fails closed outside git); the builder never grades its own work.
- CLI `atelier verify` (exit 0=PASS, 1=FAIL for CI/workflow gating).
- `scripts/workflows/memory-pillar.mjs`: the reusable pillar runner —
  snapshot → implement → INDEPENDENT verify (a distinct agent runs `atelier
  verify`). Verified end-to-end: no-op PASS on the live vault.

### Fixed — surfacing audit was blind on v7 claims (RFC 0006 P0.2b)

- `surfacing._concept_probe` built its query from pre-v7 fields
  (`touches`/`target_topic`/`title`) that v7 accepted claims do not carry, so
  every claim got an EMPTY probe and was marked dark-by-construction — the live
  audit reported 167/167 accepted learnings dark, and `eval._self_probe_block`
  counted 0 probes, silently disabling the omission gate (INV-4). Added a
  `statement` fallback (the v7 self-signal) **in the audit only** — the shared
  `recall.concept_tokens` that also drives live ranking is untouched. Live audit
  now reports 167/167 visible, self-probe R@k 1.0. No data was lost; retrieval
  was always healthy — only the measurement was broken.

### Added — foundation tooling + frozen baseline (RFC 0006 P0.2)

- `census` (node counts partitioned by kind), `baseline` generator, and a
  data-safety `snapshot` (git tag + `~/.atelier` durables tar); CLI `atelier
  snapshot`/`atelier baseline`. Froze `docs/rfc/0006-baseline.json` (the
  verification baseline the independent verifier diffs against).

### Added — memory north-star RFC (RFC 0006 P0.1)

- **`docs/rfc/0006-memory-north-star.md`** — the umbrella RFC for the memory
  system's next arc. Records the 7-issue inventory (change feed, retrieval
  misses, no forgetting, no consumer scoping, accreted topology, coarse cache,
  multi-machine), the settled decisions (one vault; lenses over walls;
  single-machine), four pillars (Grounded / Fresh / Scoped / Curated), a
  rubric framework (global invariants INV-1..4 + per-pillar goal→metric→gate
  reusing `eval.py`/`surfacing.py`), a **two-snapshot** safety+baseline
  mechanism, and a **rubric-gated workflow harness** where an independent agent
  verifies each pillar against a frozen baseline. Doc-only; no behavior change.
  Foundation tooling (baseline generator, `atelier snapshot`, verifier) follows
  in P0.2/P0.3.

### Fixed — statusline no longer melts the CPU

- **The statusline stopped calling `atelier dream --status` on every render.**
  Each render booted the full Python app and walked the whole vault
  (`dream_status()` → `_count_accepted()` is O(accepted claims)); renders
  re-fire faster than that completes, so the processes stacked and pinned
  multiple cores. The dream segment is removed from
  `scripts/hooks/statusline-atelier.sh`; the statusline now appends only the
  activity heartbeat.
- **No user-visible loss.** The dream nudge already surfaces once per session
  as a `SessionStart` `systemMessage` (`scripts/hooks/session-nudge.sh` →
  `atelier nudges --json`). The `atelier dream --status` CLI command is
  retained for tests and manual checks; only the per-render caller is gone.
  (Supersedes the [0.2.3] "User-visible dream surfaces" notes below: the
  statusline is no longer a dream-nudge surface — the `SessionStart`
  `systemMessage` is — and `atelier dream --status` was never "fast,
  filesystem-backed" at vault scale.)

### Changed — generous capture + project identity (RFC 0004 phase 2)

- **Empty `why` no longer rejects a capture.** A genuine observation with no
  `why` is now written and flagged `why_status: missing` instead of being
  dropped (`empty-why` skip removed). `require_why=True` still adds a soft
  `why_missing` nudge to the result so a live agent can re-capture with a why,
  but the candidate is kept either way. Only the `no-substance` gate (empty
  observation *and* empty why) still rejects. Realizes "generous capture,
  strict promotion."
- **`has_why` demoted MUST → SHOULD** in the acceptance-criteria template, so a
  why-less candidate is promotable (curation judges, or fills the why first).
  New `why_status` frontmatter field (`present|missing`) added to the candidate
  and accepted schemas.
- **Capture is now observable.** `_h_learning_capture` logs one line per
  outcome (`learning-capture.ok|skip|project-unknown`); previously a rejected
  or mis-keyed capture left no trace. `project-unknown` fires when a capture
  lands under a project slug no accepted learning carries yet.
- **Capture surfaces project identity confidence.** `capture()` now returns
  `project_known` from the shared resolver (`project.resolve_project`), and the
  handler logs `project-unknown` when a capture lands under a slug no accepted
  learning carries yet. (The resolver, `ProjectResolution.known`, and
  `learnings.project_map` support already existed; this only wires their signal
  through capture.)

### Changed — flat, facet-based learnings memory (RFC 0001)

Classification for the `learnings/` domain moved out of the directory path and
into indexed frontmatter **facets** resolved at query time. See
`docs/rfc/0001-flat-facet-learnings.md`.

- **Schema v5.** Accepted learnings are a flat store `learnings/notes/<YYYY-MM>/`
  (sharded by immutable creation month only); the `by-topic` canonical and
  `by-project` mirror trees are retired. New facets: `aspect[]` (project-local,
  many-valued) kept distinct from `target_topic` (global, now optional); typed
  `links:[{to,why}]` adopted. `target_project` + `aspect` + `target_topic` +
  `touches` are projected into an indexed `learning_facets(page_id,kind,value)`
  table at reindex (migration `0002`).
- **Resolver.** `search()`/`recall()` filter on the facet index (EXISTS), not a
  frontmatter scan; recall gains optional aspect/topic scoping (project stays a
  boost).
- **Absorb fixed.** The workshop→learnings absorb no longer flattens a note's
  project-local `layer` into the global `target_topic` (the "indiscriminate
  knowledge" bug); `layer`→aspect primary, `also_in`→aspect secondary, typed
  links preserved.
- **Personas retired.** The librarian/builder *agents* are gone — write-locks are
  keyed per-subtree (`wiki-write`, `learnings-write`, `captor-write`,
  `curator-write`); schema overlays renamed to space-named, the `agent:` field and
  `agents/*.md` contracts removed.
- **Dead code removed.** `reconcile.py`, the D7 doctor diagnostic, the
  `atelier_learning_reconcile` tool, and the per-project INDEX generator.
- **New scripts.** `census_damaged_learnings`, `migrate_learnings_flat`,
  `repair_lexio_layers` (the live-vault migration/repair tooling).

### Changed — unified logging on stdlib `logging`

Logging was fragmented (3 formats; Python logs depended on shell redirection;
hooks wrote separate files) and lost startup lines to stdout block-buffering.

- **`runtime/util/logging.py`** rewritten on stdlib `logging`. One consolidated,
  **append-only** sink `~/.atelier/logs/atelier.log` (override: `ATELIER_LOG_FILE`).
  Every line carries time and category:
  `2026-06-03T16:04:25+09:00 [INFO] [vault-autosync] ready vault=… interval=30`.
  Category is the first dotted segment of the message → logger name
  `atelier.<category>`, so the **33 existing call sites are unchanged**.
- `configure()` is idempotent (append survives restarts; no duplicate handlers).
  No handler targets **stdout** (stdio MCP frames stay clean); the optional
  console handler is stderr-only and TTY-gated.
- **uvicorn / mcp** library logs are bridged into the same file (`[uvicorn]`,
  `[mcp]` categories).
- `mcp_call.py` and the shell hooks (`session-bootstrap.sh`, `signal-recall.sh`
  via the new shared `scripts/hooks/_log.sh`) now write the **same format** to the
  same file. `--log` on `atelier-mcp-call` is deprecated/ignored.
- New `logging:` config block (`file`/`level`/`console`) + `LoggingConfig`.
- Old per-component logs (serve/capture/recall/bootstrap, `nohup.out`) are retired
  (not deleted).

### Added — vault auto-sync (background commit + push)

A background subsystem persists the vault to its git remote automatically
whenever data lands — from atelier's own write tools *or* direct edits.

- **`service/vault_autosync.py`** — supervisor background task
  (`server.register_background`) that polls the vault working tree on a fixed
  interval (`vault.auto_commit.interval_seconds`, default 30 s). Observer-side
  by design: it watches tree *state*, so it is source-agnostic. Commits only
  when the tree is dirty **and quiescent** (porcelain unchanged across two
  polls) — coalescing a burst into one commit without a filesystem watcher.
  The per-tick decision is the pure, unit-tested `_decide()`.
- **`sync/adapters/github.py`** — adds `commit()` (stages `-A -- .`, commits
  only if something is staged), the safety predicates `is_repo_root`,
  `in_merge_or_rebase`, `lock_present`, `dirty_porcelain`, and a **timeout**
  on all git subprocess calls (no more unbounded hangs).
- **`sync/orchestrator.commit_push()`** — vault-aware (targets `vault.local`
  once, not the two synthesized pseudo-spaces); enforces the safety gates;
  **surfaces** non-fast-forward instead of auto-merging; never raises on a
  failed push. Exposed via `atelier_sync` actions `commit` / `commit-push`.
- **`config.AutoSyncConfig`** (`vault.auto_commit` block) — `enabled` (opt-in,
  default off), `interval_seconds`, `push`, `on_conflict`, `require_stable`,
  `message_prefix`. Revives the previously dead `sync:` knobs.
- Commit messages are Conventional and **carry no AI co-author line**
  (`chore(vault): sync N change(s) [auto]` + changed-paths body).
- Caveats documented (engine-only PII guard ⇒ private remote; multi-device
  divergence is surfaced, reconciled manually). See `docs/ARCHITECTURE.md`.

## [0.2.4] — Single-vault rename regression fix + cross-domain unification

### Fixed — gorae→vault-* rename regression (v0.2 single-vault collapse)

The single-vault migration renamed the write path's space to `vault-builder`
but left read/classify/lint/promote/link paths comparing the old `gorae`
literal. Symptoms: every page classified `page_type='unknown'`, the entities
table empty, lint/promote silently no-op, doctor D2 reporting the whole vault
as phantom drift, and cross-domain wikilinks unresolved.

- **Schema-driven classification** — `runtime/index/classify.py` sources
  (path_pattern → page_type) rules from `schema/data/*.overlay.yaml` via
  `validate_v4.page_type_rules()` instead of a hardcoded table gated on
  `space=="gorae"`. Classification is now space-independent (hard-rule #3).
  Overlays gained the structural types `wiki_index`, `wiki_log`,
  `learnings_log`, `learnings_index`.
- **Space-agnostic lint + promote** — L1/L3/L5/L6 and `promote/propose`
  filter by `page_type`/slug, never a space literal; `lint.yaml` per-rule
  `spaces:` cleared.
- **D2 phantom drift** — `reindex.canonical_spaces()` is the single dedup
  source shared by `reindex_all`, doctor D2, and the D2 remediator, so the
  write and read paths can no longer disagree.

### Added — cross-domain unification (resolution-only)

- `reindex._resolve` searches all spaces and, on a slug miss, consults a
  canonical-entity alias/basename index — the same entity referenced from
  wiki, workshop and learnings resolves to one node. No new schema.

### Added — learnings mirror reconcile (D7)

- `runtime/service/learnings/reconcile.py` detects/repairs drift between the
  by-topic canonical accepted learnings and their by-project mirrors
  (orphan / duplicate / missing / divergent). Surfaced as doctor check **D7**
  and the `atelier_learning_reconcile` tool; repaired under
  `doctor(remediate=True)`.

## [0.2.3] — Capture-model correction, user-visible surfaces, hardening

### Capture model

- **Substance gate** — `atelier_learning_capture` rejects content-free
  captures (`no-substance` when observation is empty/a stub and there is
  no why; `empty-why` when an observation has no why). `require_why=True`
  by default; `absorb_claude_memory` opts out (it carries free-form
  rationale). (PR-36)
- **Capture disposition** — `scripts/hooks/capture-disposition.sh`
  (SessionStart) plants a model-context instruction so the *live agent*
  records durable lessons itself, with a real why. The old blind
  `capture-learning.sh` Stop/SessionEnd hook is deprecated. Hooks
  trigger; the agent fills the why. (PR-37)

### User-visible dream surfaces

- `atelier dream --status [--json]` — a fast, filesystem-backed one-line
  dream status (no server required). `dream.nudge_info()` is the shared
  decision source for the model nudge, the systemMessage hook, and the
  statusline. (PR-35)
- SessionStart `systemMessage` nudge (`scripts/hooks/session-nudge.sh`)
  and a statusline wrapper (`scripts/hooks/statusline-atelier.sh`,
  wrapping the user's base statusline) surface the dream nudge to the
  *user* — the session_bootstrap nudge was model-only. (PR-35)

### Review / hardening

- `atelier_learning_accept(override_must=…)` — a reviewed curator may
  override a `must` heuristic miss (e.g. free-form prose with no `## Why`
  header); the override is recorded in `ac_results`. `forbidden`
  (pii / pure-meta) is never overridable. (PR-38)
- `pii_leak` no longer false-positives on `git@…` SSH remotes or
  `*@users.noreply.github.com` addresses. (PR-38)
- DB migrations now apply when an existing file lacks the schema — an
  empty/partial DB is no longer treated as "not fresh" and skipped
  forever. (PR-39)
- accept / archive / retract prune the emptied `candidates/<date>/`
  folder they leave behind. (PR-40)
- `atelier serve` single-instance pidfile guard — a second start fails
  fast (exit 3) with a one-line message naming the running pid, instead
  of an uvicorn "address already in use" traceback. (PR-41)

### Docs

- `CLAUDE.md` hard rule #7 — atelier never mutates source material
  (`~/.claude/projects/*/memory/**`, other projects' repos); it writes
  only to its own vault. `atelier_absorb_claude_memory` is a copy, never
  a move.

## [0.2.2] — Dream cycle (automated principle synthesis)

Doc-first: the design landed in `docs/ARCHITECTURE.md`
("Learnings domain & dream cycle") before the implementing PRs. The
cycle automates *discovery* and *drafting* of cross-project principles
while keeping the high-blast-radius `always-inject` decision with a
human — and is **usage-coupled**, not scheduled, so a lid-sleeping
laptop never misses a run.

### Design (PR-34)

- ARCHITECTURE.md "Learnings domain & dream cycle": three-tier model
  (candidates / accepted / principles), bidirectional capture↔inject
  flow, cluster→synthesize→promote split, usage-coupled trigger
  rationale, and the interruption-resilience rules.

### Implementation

- **PR-29** — `atelier_learning_cluster`: deterministic **term-anchored**
  clustering (single-link agglomeration chained the whole corpus into one
  blob at scale; replaced) by shared salient terms + cross-project spread.
  `atelier_dream_status` + `mark_dream_complete` track cadence
  (filesystem-counted; markdown is truth). Also fixed a latent
  `frontmatter_json` column-name bug that silently disabled the FTS path
  in recall/search.
- **PR-30** — principle `status: proposed` tier; atomic draft writes
  (`.tmp`→`os.replace`); evidence-overlap idempotent dedup that consults
  proposed **and** accepted **and** archived (so rejected clusters are
  never re-proposed). `session_bootstrap` injects accepted-only.
- **PR-31** — `atelier_principle_{review_proposed, approve, reject}`: the
  cheap human gate. approve → accepted (optional priority override),
  reject → archived.
- **PR-32** — `session_bootstrap` dream nudge: fires on accumulation
  (≥ `nudge_after_accepted` or ≥ `nudge_after_days`) or pending proposed
  drafts. Self-healing — an interrupted dream leaves `last_dream_at`
  stale, so the nudge re-fires automatically.
- **PR-33** — `atelier_dream_plan` / `atelier_dream_complete` two-phase
  handshake (engine tees up clusters with member previews + ready-to-fill
  synthesize calls; the live agent generalizes; complete advances the
  cadence) and an `atelier dream [--complete] [--json]` CLI.

### Config

- `learnings.dream.{nudge_after_accepted, nudge_after_days}` (defaults
  15 / 7).

### Tests

181 → 200+ passing (cluster, proposed/dedup, review, nudge,
orchestration).

## [0.2.1] — Bidirectional knowledge flow with Claude Code

### Engine

- **Claude Code memory absorption** (PR-24) — `atelier_absorb_claude_memory`
  walks `~/.claude/projects/<encoded-cwd>/memory/*.md`, decodes the
  cwd, and lands each memory into atelier's learnings tier. Mapping:
  `type ∈ {feedback, reference}` → `accepted`,
  `type ∈ {user, project}` → `candidate`. Origin is captured in
  frontmatter (`source: claude-memory`, `source_path`,
  `claude_memory_type`) — *not* in a sibling topic directory, so
  topic classification stays orthogonal to origin. Deduplication by
  sha256(normalized body) cached at
  `<vault>/learnings/.absorbed-from-claude/<hash>.json`.

- **Principles tier** (PR-24.5) — `learnings/principles/` is the
  cross-project developer-ethos layer. New page_type
  `learning_principle` with frontmatter fields `coverage`
  (cross-project / single-project / single-topic) and `priority`
  (always-inject / on-relevant-prompt / manual-only). Four MCP tools:
  `atelier_principle_add`, `atelier_principle_synthesize` (draft from
  N accepted learnings; rule/why may be scaffolded), `atelier_principle_list`,
  `atelier_principle_archive`.

- **Session-start context injection** (PR-25/c) — new MCP tool
  `atelier_session_bootstrap(working_dir, max_chars=6000)` returns a
  single markdown block carrying (a) every principle with
  `priority: always-inject` and (b) the working-dir project's
  by-project learnings. Truncated bottom-up so principles never get
  clipped. Companion hook `scripts/hooks/session-bootstrap.sh` reads
  Claude Code's UserPromptSubmit payload, dedupes on `session_id`
  in `~/.atelier/cache/seen-sessions.txt`, and prints the block on
  stdout only for the first turn of each session. Loose-coupled by
  design — atelier never modifies `~/.claude/CLAUDE.md` or any
  user-owned file.

- **Auto-generated indexes** (PR-26) — `learnings/accepted/by-project/<n>/INDEX.md`
  and `learnings/principles/INDEX.md` are regenerated on every
  lifecycle event (accept / archive / retract / principle add/archive).
  Idempotent; unchanged content is not rewritten; failures on one
  entry don't block the rest. Generated files carry an
  `atelier:generated` banner so curators know not to hand-edit.

- **Per-turn signal-detector recall** (PR-28, opt-in) — new MCP tool
  `atelier_recall(query, project, top_k, max_chars)` returns the
  top-K learnings ranked by FTS5 relevance to the user's current
  prompt, with `target_project` / `project_hint` boost. Token-aware
  query sanitization survives punctuation in prompts. Filesystem
  fallback for fresh installs that haven't indexed yet. Companion
  hook `scripts/hooks/signal-recall.sh` is opt-in via
  `learnings.signal_detector.enabled: true`, with 30-second cache on
  hash(prompt) and per-session "already-shown" dedup.

### Bugs fixed

- `accept()` previously could silently overwrite a sibling accepted
  learning when two captures shared the same minute + slug. Now
  appends a numeric suffix on collision; the by-project mirror uses
  the final destination name.

### Tests

133 → 153 passing.

---

## [0.2.0] — Engine + single vault + learnings domain

### Transports — agents now attach to a running engine

- **`atelier serve` long-running asyncio supervisor** with shared SQLite
  connection, graceful SIGINT/SIGTERM shutdown, opt-in transports
  (`--stdio`, `--http`).
- **MCP stdio transport** (`runtime/service/mcp_stdio.py`) — Claude Code
  attaches via subprocess; all atelier tools exposed identically.
- **MCP HTTP transport** (`runtime/service/mcp_http.py`) — Streamable
  HTTP bound to loopback (127.0.0.1) with bearer-token middleware.
  Claude Code in any directory connects over the network to the one
  running atelier engine.
- **SpaceLockRegistry** (`runtime/service/claims.py`) — asyncio.Lock per
  WriterRole. Single-writer-per-subtree is now enforced when concurrent
  MCP callers race.
- **Session + bearer auth** (`runtime/service/auth.py`) — per-call
  Session dataclass carries agent_kind / transport / session_id /
  working_dir so future agent swaps (e.g. Hermes) need only a transport
  adapter, not engine changes.
- **Tool registry** (`runtime/service/tools.py`) — single source of MCP
  tool definitions used by both stdio and HTTP transports.
- **`atelier-mcp-call` CLI entry** (`runtime/service/mcp_call.py`) — used
  by Claude Code hook scripts to call MCP tools from the shell.

### Single vault — `gorae` is now the only memory

- **`vault:` + `subtrees:` config blocks** with strict validation;
  legacy `spaces:` accepted for one release with a deprecation path.
- **Subtree writer roles** drive lock keys
  (librarian-write / builder-write / captor-write / curator-write /
  human-only).
- **Schema v3 → v4 migrator** (`scripts/migrate_schema_v3_to_v4/`) —
  one-shot, dry-run-by-default, idempotent.
- **Workshop absorber** (`scripts/absorb_workshop/`) — copies
  `atelier-workshop/{products,notes,logs}/` into
  `<vault>/workshop/`; extracts `profile.local.yaml` files to
  `~/.atelier/profiles/`.

### Learnings domain — hook-driven developer self-memory

- **`learnings/` overlay** (`schema/data/learnings.overlay.yaml`) with
  three page types: `learning_candidate`, `learning_accepted`,
  `learning_archived`. Candidates are append-only.
- **Acceptance criteria** with `criteria.yaml` (in-vault, user-editable)
  and a self-check covering has_why / is_specific / is_actionable /
  tied_to_event / has_project_tag / novel / retracted / pii_leak /
  pure_meta.
- **Lifecycle tools** (MCP): `atelier_learning_capture` (captor),
  `atelier_learning_review_pending` (read), `atelier_learning_accept`
  (curator, must-checks pass to promote), `atelier_learning_archive`
  (curator), `atelier_learning_retract` (curator, also from accepted),
  `atelier_learning_search` (read), `atelier_learning_relink` (curator).
- **Hook adapter** (`scripts/hooks/capture-learning.sh`) — installable
  template for Claude Code Stop / SessionEnd hooks. Always exits 0 so a
  failing capture never blocks the user's flow.
- **`memory/` → `learnings/by-{topic,project}/` absorber**
  (`scripts/absorb_workshop_memory_to_learnings/`).

### Capability ports — atelier absorbs the proto-engine

The proto-engine's standalone Python scripts in the content repo are
now atelier MCP tools. The corresponding gorae files become deletable
after operators run the migration + absorption scripts:

| MCP tool                  | Replaces gorae script          |
|---------------------------|--------------------------------|
| `atelier_validate`        | `validate_metadata.py`         |
| `atelier_fix_pending`     | `fix_pending_entries.py`       |
| `atelier_index_regen`     | `update_wiki_index.py`         |
| `atelier_prepare_commit`  | `prepare.py` + `pre_commit_update.py` (mechanical parts; LLM facets reclass deferred to v0.3) |
| `atelier_clip_image`      | `clip_images.py` + `r2_upload.py` glue |
| `atelier_new_doc`         | `create_document.py`           |
| `atelier_youtube`         | `ingest_youtube.py` (yt-dlp + VTT; OpenAI STT fallback gated on credentials) |

A consolidated operator checklist for removing the proto-engine lives
at `scripts/gorae_cleanup/CHECKLIST.md`.

### Tests

114 → 120+ pytest tests covering serve lifecycle, claims locking, bearer
auth, MCP tool registry, vault config dual-read, schema migration,
workshop absorption, learnings lifecycle (capture/review/accept/archive/
retract/search/relink), and every capability port.

### Optional dependencies

- `[serve]`: `mcp>=1.0`, `httpx>=0.28`
- `[youtube]`: `yt-dlp>=2025.1`, `openai>=1.50`

### Backlog deferred to v0.3+

- LLM facets reclassification on prepare_commit
- OpenAI STT path on YouTube ingest when subtitles are absent
- Discord transport (out of scope by user decision)
- OAuth for MCP HTTP (currently static bearer + loopback only)
- launchd / autostart (foreground-only by user decision)
- Full R2 sync adapter (still stub)
- Automatic AC scoring on learnings (currently human-in-the-loop only)

---

## [0.1.0] — Initial public release

First release of the engine. Built to operate on private user content via
runtime config, with zero user-specific bindings in the engine itself.

### Engine

- **Schema v4** as data — `schema/data/{base, librarian.overlay, builder.overlay,
  linking, lint}.yaml` + `schema/db/sql/0001_initial.sql`.
- **Two-steward agent contracts** — `agents/{librarian, builder}.md`
  (culture-neutral; voice overlays loaded from `~/.atelier/voices/`).
- **Indexing pipeline** — `runtime/index/{crawl, parse, linker, classify,
  entities, writeback, reindex}`. Markdown → SQLite + FTS5 (`unicode61`).
- **Search** — `runtime/search/{fts, graph, render}`.
- **Lint** — `runtime/lint/{L1, L3, L5, L6}` driven by `lint.yaml`.
- **Doctor** — `runtime/doctor/{diagnostics, remediate}` for D1–D6.
- **Sync adapters** — `runtime/sync/adapters/{github, r2, local_fs}` (R2
  adapter is a stub; full impl in v0.2).
- **Service shape** — `runtime/service/{api, auth, claims, capture}`. All
  CLI commands route through `service.api` to keep the door open for MCP
  and HTTPS surfaces in v0.2.
- **Promote pipeline** — `runtime/promote/{propose, apply}` for
  workshop → wiki promotion with `PROMOTION_LOG.md`.
- **CLI** — `atelier {setup, reindex, search, links, list, lint, doctor,
  sync, capture, new-product, promote}`.
- **Strict config validation** — refuses to start if `~/.atelier/config.yaml`
  contains placeholder values (`<...>`, `REQUIRED`, `your-`, `path/to/your`).
- **Role-based space lookup** — `cfg.space_by_role()` for engine code that
  needs to reach a steward's territory without naming the space.

### Tests

16 pytest tests covering schema yaml validity, parse/classify/linker, FTS
search + graph traversal, and L1/L3/L5 lint rules end-to-end.

### Docs

- `docs/ARCHITECTURE.md` — engine contract and component map.
- `docs/SCHEMA_V4.md` — schema v4 reference.
- `docs/ADOPTING.md` — third-party adoption guide.
- `docs/OPS_NOTES.md` — operational soak runbook.
- `docs/_archive/IMPLEMENTATION_LOG.md` — historical v0.1 build plan
  (preserved for context; references the original adopter's space names).

### Known v0.2 backlog (engine-contract audit)

- **Level 3 — Role-based dispatch** (currently partial): `runtime/index/classify.py`
  and `runtime/index/linker.py` still key on literal space names. Schema
  overlays (`librarian.overlay.yaml`, `builder.overlay.yaml`,
  `lint.yaml`) declare `spaces: [...]` literals instead of `roles: [...]`.
  v0.2 will complete the cutover.
- **Full R2 sync adapter** (currently stub).
- **L2 hallucination lint** (LLM-dependent).
- **Vector / hybrid search** (currently FTS5 keyword only).
- **MCP and HTTPS surfaces** via `runtime/service/api` (currently CLI only).
- **Mobile capture endpoint** activation (function exists, no HTTP surface).
- **Real auth / claims enforcement** (currently placeholder for single-user
  trusted-client mode).
