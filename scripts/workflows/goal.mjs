// RFC 0009 §6 — the goal convergence loop.
//
// A goal declares a machine-checkable delta contract BEFORE any code is written,
// then converges the change against it. The shape is deliberate:
//
//   Snapshot   rollback point + round baseline (the current before-picture)
//   Contract   author the contract → a CRITIC (distinct agent) must accept it,
//              because a bad contract cannot be caught after the fact — by then
//              the implementation defines the target
//   Implement  builder
//   Verify     independent verifier runs `atelier goal-verify`
//     ├ PASS ────────────────────────────────────────────→ Ship
//     ├ FAIL, round < 3 → FIXER gets ONLY the failing checks → Verify
//     ├ FAIL, round = 3 → abort (git discard + snapshot restore) + escalate
//     └ HARD ABORT (exit 2) → never retried in-round; a broken pin or unknown
//                             metric key means the harness is untrustworthy
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
const snap = await agent(
  `Freeze a rollback point and capture the round baseline for goal ${GOAL_ID}.\n` +
  `1. Run: atelier snapshot create — parse the snapshot id.\n` +
  `2. Reindex, then capture the round baseline: generate a baseline over the live ` +
  `vault and write it to ~/.atelier/cache/goals/${GOAL_ID}/before.json, INCLUDING ` +
  `its per-file digest map (the _file_digests key) so a fingerprint waiver can be ` +
  `scored. Return the snapshot id and the before.json path. Change nothing else.`,
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
  `captured_at_head = the CURRENT HEAD, which becomes the contract commit's first ` +
  `parent; fixture_sha256 if a probe fixture is used). Do NOT implement anything yet. ` +
  `Summarize the contract for the critic.`,
  { label: 'author', phase: 'Contract' })

const critic = await agent(
  `You are the CRITIC for goal ${GOAL_ID}. You did NOT author this contract. Your one ` +
  `job (RFC 0009 §6): reject a bound satisfiable WITHOUT achieving the goal.\n\n` +
  `Check: every intended change has an INTENT clause with an exact bound; no bound is ` +
  `a rubber stamp (a meaningless min/max that a regression would still pass); every ` +
  `waiver names a real reason and a real bound; every supersedes entry has a matching ` +
  `INTENT bound; the pins are present. If it holds, COMMIT it: git add docs/goals/${GOAL_ID}.json ` +
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
    `the implementer.\n1. Reindex the vault.\n2. Run: atelier goal-verify --contract ${contractPath} ` +
    `--before ${snap ? snap.before_path : '<before.json>'}. Exit 0 = PASS, 1 = FAIL, 2 = HARD ABORT.\n` +
    `3. Read the printed JSON. Return outcome (pass|fail|abort), the failing check keys, and a ` +
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
  `the delta matches contract ${contractPath}. Do NOT fix, commit, push, or merge.`,
  { label: 'review', phase: 'Ship', schema: REVIEW_SCHEMA })

if (!review) {
  // A raise, not a FAIL: independence is unavailable, so the bar is unmeetable.
  log(`review unavailable — cannot satisfy the independent-review bar; not shipping`)
  return { goalId: GOAL_ID, outcome: 'review-unavailable', contract: contractPath,
           snapshot: snap ? snap.snapshot_id : null, merge: 'blocked' }
}
const musts = review.must || []
const pr = await agent(
  `Open a PR for goal ${GOAL_ID} (verified delta per ${contractPath}). Push the implement ` +
  `branch (branch off main first if you are on main) and \`gh pr create\` describing the goal ` +
  `and its delta. Post the independent review findings as a PR comment. ` +
  `Do NOT merge — merge is a human act. NEVER pass --no-verify (the pre-commit guard is ` +
  `mandatory). Author gorae <kyuhyunhaan@gmail.com>, no Co-Authored-By. Return the PR URL.`,
  { label: 'open-pr', phase: 'Ship', schema: PR_SCHEMA })

return {
  goalId: GOAL_ID,
  outcome: musts.length === 0 ? 'passed-awaiting-merge' : 'blocked-on-must',
  contract: contractPath,
  snapshot: snap ? snap.snapshot_id : null,
  pr: pr ? pr.url : null,
  review: { must: musts, should: review.should || [], summary: review.summary },
  merge: 'awaiting-human',   // §9: the merge is the human's, always
}
