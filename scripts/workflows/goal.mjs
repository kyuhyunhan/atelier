// RFC 0009 §6 — the goal convergence loop.
//
// A goal declares a machine-checkable delta contract BEFORE any code is written,
// then converges the change against it. The shape is deliberate:
//
//   Snapshot   clean-tree preflight, then rollback point + round baseline. The
//              tree MUST start clean: the before baseline, the vault fingerprint,
//              and the isolatable commit delta all assume the only later changes
//              are the goal's own.
//   Contract   author the contract → a CRITIC (distinct agent) must accept it,
//              because a bad contract cannot be caught after the fact — by then
//              the implementation defines the target
//   Implement  builder
//   Verify     independent verifier runs `atelier goal-verify`
//     ├ PASS ────────────────────────────────────────────→ Commit → Ship
//     ├ FAIL, round < 3 → FIXER gets ONLY the failing checks → Verify
//     ├ FAIL, round = 3 → abort (git discard + snapshot restore) + escalate
//     └ HARD ABORT (exit 2) → never retried in-round; a broken pin or unknown
//                             metric key means the harness is untrustworthy
//   Commit     the converged working tree lands on the branch, ONCE, so the
//              reviewer audits exactly what will merge
//   Ship       ship-pr (its own independent review loop)
//
// The critic gates the CONTRACT, not the code; the fixer receives failing checks
// only, not the builder's narrative — handing over the builder's own account of
// what it did reintroduces the self-grading the independent verifier exists to
// prevent.
//
// Invoke with args, e.g.:
//   { goalId: "G1-pii-liveness",
//     goal: "Load the PII guard: >=1 active pattern, L1 lint clean.",
//     intentHint: "pii_active_patterns >= 1; lint.L1 == 0" }
export const meta = {
  name: 'goal',
  description: 'Run one RFC 0009 goal: snapshot, author+critique a delta contract, implement, then converge against it with an independent verifier.',
  phases: [
    { title: 'Snapshot', detail: 'rollback point + round baseline' },
    { title: 'Contract', detail: 'author the delta contract; a critic accepts it' },
    { title: 'Implement', detail: 'the builder makes the change' },
    { title: 'Verify', detail: 'independent verify → fix loop, max 3 rounds' },
    { title: 'Commit', detail: 'land the verified tree on the branch, before review' },
    { title: 'Ship', detail: 'ship-pr on convergence' },
  ],
}

// Normalize args, then fail fast on a genuinely missing goal. The runtime here
// delivers `args` as a JSON STRING even when the caller passes an object, so a
// bare `args.goalId` is undefined and the script would silently fall back to a
// placeholder goal — from which the author agent fabricated a no-op contract in
// an earlier run (every clause an identity delta; the critic rightly rejected it,
// but a scaffold goal should never reach the author). Parse a string form rather
// than reject it, since that is how the value actually arrives; then require the
// three fields so an undeclared goal is a loud caller error, not three wasted
// agents.
let _args = args
if (typeof _args === 'string') {
  try { _args = JSON.parse(_args) } catch (e) {
    throw new Error('goal workflow: args is a string that is not valid JSON: ' + e)
  }
}
if (!_args || typeof _args !== 'object' || !_args.goalId || !_args.goal || !_args.intentHint) {
  throw new Error(
    'goal workflow requires args {goalId, goal, intentHint}. Got: ' + JSON.stringify(args))
}
const GOAL_ID = _args.goalId
const GOAL = _args.goal
const INTENT_HINT = _args.intentHint
const MAX_ROUNDS = 3

const SNAP_SCHEMA = {
  type: 'object',
  properties: { snapshot_id: { type: 'string' }, before_path: { type: 'string' } },
  required: ['snapshot_id', 'before_path'],
}
const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    accepted: { type: 'boolean' },
    contract_path: { type: ['string', 'null'] },
    objections: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['accepted', 'summary'],
}
const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    outcome: { type: 'string', enum: ['pass', 'fail', 'abort'] },
    failing_checks: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['outcome', 'summary'],
}
const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    must: { type: 'array', items: { type: 'string' } },
    should: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['must', 'summary'],
}
const PR_SCHEMA = {
  type: 'object',
  properties: { url: { type: ['string', 'null'] } },
  required: ['url'],
}

// ── Snapshot ────────────────────────────────────────────────────────────────
phase('Snapshot')
// Clean-tree preflight. The Commit stage stages the converged tree with `git add
// -A`; on a dirty checkout that would absorb foreign in-tree files (a stray doc,
// prior WIP) into the goal's implementation commit. The before baseline and vault
// fingerprint are likewise computed over the current tree. All three assume the
// only changes by commit time are the goal's own — so refuse to start dirty rather
// than silently commit or measure someone else's uncommitted work. (~/.atelier is
// out of tree, so its churn never counts.)
const preflight = await agent(
  `Preflight for goal ${GOAL_ID}: run \`git status --porcelain\` in the repo. Return ` +
  `clean=true iff the output is EMPTY — no staged, unstaged, or untracked in-tree ` +
  `changes. If not empty, list the offending paths. Do NOT modify, stage, commit, or ` +
  `clean anything.`,
  { label: 'preflight', phase: 'Snapshot',
    schema: { type: 'object',
              properties: { clean: { type: 'boolean' },
                            paths: { type: 'array', items: { type: 'string' } } },
              required: ['clean'] } })
if (!preflight || !preflight.clean) {
  const dirty = preflight ? (preflight.paths || []).join(', ') : 'preflight agent failed'
  log(`preflight: working tree not clean — refusing to start (${dirty})`)
  return { goalId: GOAL_ID, stage: 'preflight', outcome: 'dirty-tree',
           paths: preflight ? preflight.paths || [] : [] }
}

const snap = await agent(
  `Freeze a rollback point and capture the round baseline for goal ${GOAL_ID}.\n` +
  `1. Run: atelier snapshot create — parse the snapshot id.\n` +
  `2. Reindex, then capture the round baseline with BOTH guards on — one command, ` +
  `exactly as written:\n` +
  `   atelier baseline --strict-engine --with-file-digests --out ` +
  `~/.atelier/cache/goals/${GOAL_ID}/before.json\n` +
  `   --with-file-digests is REQUIRED: without the per-file map a fingerprint ` +
  `waiver's changed_paths bound cannot be scored at all (it fails silently, at ` +
  `verify). --strict-engine refuses (exit 2, reason on stderr, nothing written) ` +
  `to pin a baseline verify cannot reproduce. Do NOT set ATELIER_EMBED=off for ` +
  `this command: that kill switch is this repo's convention for TEST runs, and ` +
  `using it freezes eval.engine as lexical-rrf while verify measures hybrid — ` +
  `the run then hard-aborts AFTER the implementation is written (it cost a full ` +
  `G7 round). If the command refuses, STOP and report its stderr reason verbatim ` +
  `— do not retry with the switch on and do not hand-edit the baseline.\n` +
  `3. Return the snapshot id and the before.json path. Change nothing else.`,
  { label: 'snapshot', phase: 'Snapshot', schema: SNAP_SCHEMA })

log(`snapshot ${snap ? snap.snapshot_id : '(failed)'}; before at ${snap ? snap.before_path : '?'}`)

// ── Contract (author → critic gate) ──────────────────────────────────────────
phase('Contract')
const author = await agent(
  `Author the RFC 0009 delta contract for goal ${GOAL_ID}: "${GOAL}".\n` +
  `Intended change: ${INTENT_HINT}.\n\n` +
  `Write docs/goals/${GOAL_ID}.json with: intent clauses (metric + bound, from the ` +
  `round baseline's actual values), a default-deny envelope with any needed bounded ` +
  `waivers, supersedes entries only if an invariant must be released (each with a ` +
  `matching INTENT bound), and the pins block (before_sha256 = sha256 of before.json; ` +
  `captured_at_head = the CURRENT HEAD on main, which becomes the contract commit's first ` +
  `parent; fixture_sha256 whenever an out-of-tree fixture backs ANY metric in this run's ` +
  `namespace — INTENT *or* ENVELOPE, since --fixture feeds only the pin check and a metric ` +
  `is measured from the default fixture either way, so an unpinned enveloped leaf can be ` +
  `moved by a mid-run fixture edit with nothing to catch it. When you pin it you MUST also ` +
  `write a top-level "fixture_path" field BESIDE (not inside) the pins block, holding that ` +
  `fixture's path. The verifier reads fixture_path to pass --fixture, and check_pins RAISES ` +
  `on a pin with no declared path — prose in a note does not count. Omit both when no ` +
  `out-of-tree fixture applies.) ` +
  `Do NOT implement anything yet. Summarize the contract for the critic.`,
  { label: 'author', phase: 'Contract' })

const critic = await agent(
  `You are the CRITIC for goal ${GOAL_ID}. You did NOT author this contract. Your one ` +
  `job (RFC 0009 §6): reject a bound satisfiable WITHOUT achieving the goal.\n\n` +
  `Check: every intended change has an INTENT clause with an exact bound; no bound is ` +
  `a rubber stamp (a meaningless min/max that a regression would still pass); every ` +
  `waiver names a real reason and a real bound; every supersedes entry has a matching ` +
  `INTENT bound; the pins are present — and if pins.fixture_sha256 is non-null, a top-level ` +
  `"fixture_path" field must sit BESIDE the pins block (check_pins raises without it, so a ` +
  `contract missing it burns the whole Implement stage before aborting at verify). ` +
  `If it holds: FIRST create and switch to a fresh ` +
  `feature branch \`feat/rfc-0009-${GOAL_ID}\` off the current main HEAD ` +
  `(\`git switch -c\`; if it already exists from an aborted prior run, delete it first ` +
  `with \`git branch -D\` so this run starts clean) — so the contract ` +
  `commit's first parent stays that HEAD = captured_at_head, AND every later implement/fix ` +
  `commit lands on the branch — the independent reviewer diffs \`main...HEAD\`, so work on ` +
  `main would leave that diff empty and the review vacuous). THEN commit: ` +
  `git add docs/goals/${GOAL_ID}.json ` +
  `&& commit (author gorae <kyuhyunhaan@gmail.com>, no Co-Authored-By), and record the ` +
  `critic acceptance in the contract's critic block. If not, return accepted=false with ` +
  `objections. The author's summary, for cross-reference only:\n${author || '(none)'}`,
  { label: 'critic', phase: 'Contract', schema: CRITIC_SCHEMA })

if (!critic || !critic.accepted) {
  log(`contract rejected: ${critic ? critic.summary : '(critic failed)'}`)
  return { goalId: GOAL_ID, stage: 'contract', accepted: false,
           objections: critic ? critic.objections : ['critic did not run'] }
}
const contractPath = critic.contract_path || `docs/goals/${GOAL_ID}.json`
log(`contract accepted and committed at ${contractPath}`)

// ── Implement ─────────────────────────────────────────────────────────────────
phase('Implement')
// The builder's summary is deliberately NOT captured: §6 keeps it away from the
// fixer, which receives only the verifier's failing checks. Letting the builder's
// own account of what it did flow downstream reintroduces the self-grading the
// independent verifier exists to prevent.
await agent(
  `Implement goal ${GOAL_ID}: "${GOAL}". A committed contract at ${contractPath} defines ` +
  `the target; a snapshot (${snap ? snap.snapshot_id : 'see stage 1'}) makes this ` +
  `reversible. Constraints: markdown is truth, the DB is a projection; reuse existing ` +
  `predicates/helpers; add tests. Do NOT touch the contract or the round baseline. ` +
  `Summarize what changed.`,
  { label: 'implement', phase: 'Implement' })

// ── Verify → fix loop ─────────────────────────────────────────────────────────
phase('Verify')
let lastFailing = []
let verdict = null
for (let round = 1; round <= MAX_ROUNDS; round++) {
  verdict = await agent(
    `You are the INDEPENDENT verifier for goal ${GOAL_ID}, round ${round}. Do NOT trust ` +
    `the implementer.\n1. Reindex the vault.\n2. Read the contract at ${contractPath}: if its ` +
    `pins block carries a non-null fixture_sha256, the contract MUST also record the fixture's ` +
    `path (a fixture_path field beside the pins); expand ~ and pass it as --fixture. A pinned ` +
    `fixture with no locatable path is itself a hard abort — report it, do not guess a path.\n` +
    `3. Run: atelier goal-verify --contract ${contractPath} ` +
    `--before ${snap ? snap.before_path : '<before.json>'} [--fixture <path if pinned>]. ` +
    `Exit 0 = PASS, 1 = FAIL, 2 = HARD ABORT.\n` +
    `4. Read the printed JSON. Return outcome (pass|fail|abort), the failing check keys, and a ` +
    `one-line summary. Do NOT fix anything.`,
    { label: `verify:r${round}`, phase: 'Verify', schema: VERIFY_SCHEMA })

  if (!verdict || verdict.outcome === 'abort') {
    log(`round ${round}: HARD ABORT — ${verdict ? verdict.summary : 'verifier failed'}`)
    await agent(
      `Goal ${GOAL_ID} hit a HARD ABORT (a broken pin, unknown metric key, or corrupt ` +
      `map — the harness is untrustworthy). Restore: atelier snapshot restore ` +
      `${snap ? snap.snapshot_id : '<id>'}, and discard the implement branch. Do not retry.`,
      { label: 'abort-restore', phase: 'Verify' })
    return { goalId: GOAL_ID, stage: 'verify', outcome: 'abort',
             summary: verdict ? verdict.summary : 'verifier failed' }
  }
  if (verdict.outcome === 'pass') {
    log(`round ${round}: PASS`)
    break
  }
  // FAIL
  lastFailing = verdict.failing_checks || []
  if (round === MAX_ROUNDS) {
    log(`round ${round}: FAIL, non-convergence — restoring and escalating`)
    await agent(
      `Goal ${GOAL_ID} did not converge in ${MAX_ROUNDS} rounds. Discard the implement ` +
      `branch (git) and, if the run mutated the vault, atelier snapshot restore ` +
      `${snap ? snap.snapshot_id : '<id>'}. Report the open failing checks: ${lastFailing.join(', ')}.`,
      { label: 'nonconverge-restore', phase: 'Verify' })
    return { goalId: GOAL_ID, stage: 'verify', outcome: 'nonconverged',
             failing_checks: lastFailing }
  }
  log(`round ${round}: FAIL (${lastFailing.join(', ')}) → fixer`)
  await agent(
    `You are the FIXER for goal ${GOAL_ID}. The independent verifier reported these ` +
    `FAILING checks and NOTHING else — do not ask what the builder did, address only ` +
    `these:\n${lastFailing.map((f) => `  - ${f}`).join('\n')}\n\n` +
    `Adjust the implementation (not the contract, not the round baseline) so each is ` +
    `satisfied, keeping tests green. Summarize the fix.`,
    { label: `fix:r${round}`, phase: 'Verify' })
}

// ── Commit — the verified tree lands on the branch BEFORE review ────────────────
//
// Verify runs `atelier goal-verify` against the WORKING TREE (files on disk), so on
// convergence the implementation is correct but UNCOMMITTED — only the contract JSON
// is committed (by the critic). If review ran now it would diff `main...HEAD`, see
// only the contract, and raise a false "empty diff: nothing was implemented" MUST
// while the real code sat uncommitted — then the open-pr agent would commit it when
// it pushed, so a PASSING goal shipped blocked-on-a-stale-must (the first G5 run hit
// exactly this). Commit the converged tree HERE, once, so the reviewer audits
// precisely what will merge. This also turns the review's empty-diff check into a
// true backstop: a genuinely empty implementation commits nothing, leaving only the
// contract on the branch, and the MUST fires for real.
phase('Commit')
await agent(
  `Commit the verified implementation for goal ${GOAL_ID} onto the feature branch ` +
  `feat/rfc-0009-${GOAL_ID}. The contract is already committed by the critic; stage and ` +
  `commit the implementation and its tests (\`git add -A\` on the repo — ~/.atelier is ` +
  `out of tree, so nothing there is staged). Author gorae <kyuhyunhaan@gmail.com>, no ` +
  `Co-Authored-By, a Conventional message naming the goal. NEVER pass --no-verify — the ` +
  `pre-commit PII guard is mandatory. Do NOT modify the contract or the round baseline. ` +
  `If there is nothing to commit (no implementation change on disk), say so plainly and ` +
  `commit nothing — never fabricate a change to make the tree non-empty.`,
  { label: 'commit', phase: 'Commit' })

// ── Ship — review by the ORCHESTRATOR, merge by a HUMAN ─────────────────────────
//
// RFC 0009 §9 lists "autonomous merging of a goal" as a non-goal; the first live
// run merged its own PR anyway, because the ship stage was one agent that did
// push + PR + review + merge, and its independence was self-attested (the exact
// shape §3.1.1 rejects). So review moves HERE, spawned by the orchestrator as a
// DISTINCT agent, and the orchestrator — not the ship agent's narrative — decides
// whether the bar is met, from the reviewer's structured return.
//
// "Cannot obtain an independent review" is a RAISE (the harness cannot be trusted
// for this run), never a FAIL to route around by self-reviewing. So a reviewer
// that does not run means no PR claiming review and no merge — same never-reaches-
// merge semantics as the abort branch above. And merge itself stays human: it is
// the one per-goal act §9 always kept outside the loop.
phase('Ship')
const review = await agent(
  `You are the INDEPENDENT reviewer for goal ${GOAL_ID}. You did NOT build this change. ` +
  `Read-only audit of \`git diff main...HEAD\` against the ship-pr rubric and the ` +
  `CLAUDE.md invariants. Tag findings [MUST]/[SHOULD]/[NIT]/[Q] with file:line. Verify ` +
  `the delta matches contract ${contractPath}. FIRST run \`git diff --stat main...HEAD\` ` +
  `and confirm it changes implementation files, NOT just the contract: \`main...HEAD\` ` +
  `always carries the critic's ${contractPath} commit, so a diff that touches ONLY that ` +
  `file means the implementation was never committed — return that as a [MUST] ("no ` +
  `implementation committed: diff is contract-only"), never a clean pass. Do NOT fix, ` +
  `commit, push, or merge.`,
  { label: 'review', phase: 'Ship', schema: REVIEW_SCHEMA })

if (!review) {
  // A raise, not a FAIL: independence is unavailable, so the bar is unmeetable.
  log(`review unavailable — cannot satisfy the independent-review bar; not shipping`)
  return { goalId: GOAL_ID, outcome: 'review-unavailable', contract: contractPath,
           snapshot: snap ? snap.snapshot_id : null, merge: 'blocked' }
}
const musts = review.must || []
const pr = await agent(
  `Open a PR for goal ${GOAL_ID} (verified delta per ${contractPath}). The implementation ` +
  `is ALREADY committed (Commit stage) — do NOT create new commits; just \`git push\` the ` +
  `feature branch \`feat/rfc-0009-${GOAL_ID}\` and \`gh pr create\` describing the goal and ` +
  `its delta${musts.length ? ' AS A DRAFT (open MUST findings remain)' : ''}. Post the ` +
  `independent review findings as a PR comment. Do NOT merge — merge is a human act. ` +
  `NEVER pass --no-verify (the pre-commit guard is mandatory). Author gorae ` +
  `<kyuhyunhaan@gmail.com>, no Co-Authored-By. Return the PR URL.`,
  { label: 'open-pr', phase: 'Ship', schema: PR_SCHEMA })

if (!pr || !pr.url) {
  // The open-pr agent failed to produce a PR. Reporting "passed" with no PR is
  // the false-success §9 guards against — treat a missing PR as a raise.
  log(`open-pr produced no PR URL — nothing to hand to the human; not claiming a pass`)
  return { goalId: GOAL_ID, outcome: 'ship-failed', contract: contractPath,
           snapshot: snap ? snap.snapshot_id : null, pr: null,
           review: { must: musts, should: review.should || [], summary: review.summary },
           merge: 'blocked' }
}

return {
  goalId: GOAL_ID,
  outcome: musts.length === 0 ? 'passed-awaiting-merge' : 'blocked-on-must',
  contract: contractPath,
  snapshot: snap ? snap.snapshot_id : null,
  pr: pr.url,
  review: { must: musts, should: review.should || [], summary: review.summary },
  // A clean review awaits the human's merge (§9); open MUSTs are `blocked`, the
  // same signal the review-unavailable/ship-failed raises use, so a consumer
  // keying on `merge` never reads a MUST-blocked draft as mergeable.
  merge: musts.length === 0 ? 'awaiting-human' : 'blocked',
}
