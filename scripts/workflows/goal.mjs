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

const GOAL_ID = (args && args.goalId) || 'G-unnamed'
const GOAL = (args && args.goal) || 'Implement the goal as specified.'
const INTENT_HINT = (args && args.intentHint) || '(state the intended metric changes)'
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

// ── Ship ──────────────────────────────────────────────────────────────────────
phase('Ship')
return {
  goalId: GOAL_ID,
  outcome: 'passed',
  contract: contractPath,
  snapshot: snap ? snap.snapshot_id : null,
  ship: await agent(
    `Goal ${GOAL_ID} converged (contract PASS). Ship it via the ship-pr flow: push the ` +
    `implement branch, open a PR describing the goal and its verified delta, run the ` +
    `independent review loop, and merge when the bar is met. Author gorae ` +
    `<kyuhyunhaan@gmail.com>, no Co-Authored-By.`,
    { label: 'ship', phase: 'Ship' }) || '(see ship-pr)',
}
