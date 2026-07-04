// RFC 0006 pillar runner — snapshot → implement → INDEPENDENT verify.
//
// The whole point of the shape: the agent that IMPLEMENTS a pillar is not the
// agent that decides it passed. Stage 3 gets a fresh agent, handed only the
// rubric + the frozen baseline, and it runs `atelier verify` (exit 0/1 is the
// gate). Stage 1 freezes a rollback point first, so a failed pillar is fully
// reversible.
//
// Invoke via the Workflow tool with args, e.g.:
//   { pillar: "① Grounded", rubric: "P0", baseline: "docs/rfc/0006-baseline.json",
//     task: "Add the vault manifest + lens vocabulary in schema/data (RFC 0006 §7①)." }
export const meta = {
  name: 'memory-pillar',
  description: 'Run one RFC 0006 memory pillar: snapshot, implement, then verify with an independent agent against the frozen baseline.',
  phases: [
    { title: 'Snapshot', detail: 'freeze a data-safety rollback point' },
    { title: 'Implement', detail: 'the pillar change (the only stage that writes)' },
    { title: 'Verify', detail: 'independent agent scores after-state vs baseline' },
  ],
}

const PILLAR = (args && args.pillar) || '(unspecified pillar)'
const RUBRIC = (args && args.rubric) || 'P0'
const BASELINE = (args && args.baseline) || 'docs/rfc/0006-baseline.json'
const TASK = (args && args.task) || 'Implement the pillar as specified in RFC 0006.'

const SNAP_SCHEMA = {
  type: 'object',
  properties: { snapshot_id: { type: 'string' }, tag: { type: ['string', 'null'] } },
  required: ['snapshot_id'],
}
const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    passed: { type: 'boolean' },
    failing_checks: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['passed', 'summary'],
}

phase('Snapshot')
const snap = await agent(
  `Freeze a data-safety rollback point before any change to the memory vault. ` +
  `Run: atelier snapshot create. Parse its output and return the snapshot id (the ` +
  `timestamp, e.g. 20260704T073000Z) and the git tag if present. Do not change ` +
  `anything else.`,
  { label: 'snapshot', phase: 'Snapshot', schema: SNAP_SCHEMA })

log(`snapshot: ${snap ? snap.snapshot_id : '(failed)'} — proceeding to implement ${PILLAR}`)

phase('Implement')
const impl = await agent(
  `Implement RFC 0006 pillar ${PILLAR}. ${TASK}\n\n` +
  `Constraints: markdown is truth, the DB is a projection (never write the DB as ` +
  `a sole source); reuse existing predicates/helpers; add tests. When done, ` +
  `summarize exactly what changed (files + behavior) so an independent verifier ` +
  `can check it. A data-safety snapshot (${snap ? snap.snapshot_id : 'see stage 1'}) ` +
  `exists, so this is reversible.`,
  { label: `implement:${PILLAR}`, phase: 'Implement' })

phase('Verify')
// A DISTINCT agent — it did not write the change, and it is told not to trust the
// implementer's account. It runs the verifier; the exit code is the gate.
const verdict = await agent(
  `You are the INDEPENDENT verifier for RFC 0006 pillar ${PILLAR}. Do NOT trust ` +
  `the implementer's summary; verify from the tools.\n\n` +
  `1. Ensure the vault is reindexed: run \`atelier reindex --space gorae\`.\n` +
  `2. Run: \`atelier verify --baseline ${BASELINE} --rubric ${RUBRIC}\`. Exit 0 = PASS, ` +
  `1 = FAIL. Read the printed JSON report.\n` +
  `3. Return whether it passed, the names+details of any failing GATE checks, and a ` +
  `one-line summary. If a gate failed, the pillar must be fixed or rolled back ` +
  `(atelier snapshot restore ${snap ? snap.snapshot_id : '<id>'}).\n\n` +
  `The implementer's account, for cross-reference only (do not take it as truth):\n` +
  `${impl || '(no summary returned)'}`,
  { label: `verify:${PILLAR}`, phase: 'Verify', schema: VERDICT_SCHEMA })

return {
  pillar: PILLAR,
  rubric: RUBRIC,
  snapshot: snap,
  verdict,
  passed: verdict ? verdict.passed : false,
}
