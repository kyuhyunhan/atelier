# Retrieval benchmarks

Self-measured numbers for the claims the README makes. Methodology first,
because numbers without it are marketing.

## Method

- **Harness**: `runtime/service/learnings/eval.py` — the same eval the RFC 0009
  verification gates run. Reproduce with `atelier verify` machinery or directly:
  `python3 -c "from runtime.service.learnings import eval as ev; print(ev.run(k=5))"`.
- **Probe sets**: `self_probe` (every accepted learning searched by its own
  concept, n=201), `paraphrase` (22 frozen query→gold pairs written as
  *differently-worded* questions — the semantic-headroom set,
  `tests/fixtures/paraphrase_probes.json`).
- **Engines**: `lexical-rrf` (FTS5 + RRF, `ATELIER_EMBED=off`) vs `hybrid`
  (lexical + `bge-m3` vectors via local Ollama, RRF-fused). Each measured in an
  isolated process on the same vault snapshot (4,486 claims), 2026-08-03.

## Numbers

| Probe set | Metric | lexical-rrf | hybrid | Δ |
|---|---|---|---|---|
| paraphrase (22) | Recall@5 | 0.636 | **0.773** | **+13.6 pp** |
| paraphrase (22) | MRR | 0.583 | **0.652** | +6.8 pp |
| self_probe (201) | Recall@5 | 1.000 | 0.990 | −1.0 pp |
| self_probe (201) | MRR | 0.937 | 0.931 | −0.6 pp |

Read it as designed: hybrid pays off exactly where wording diverges from
storage (paraphrase), at the cost of displacing 2 of 201 self-probes out of
the top-5. If you never ask differently-worded questions, lexical is already
at ceiling on this vault.

## Honest limits

- **Not comparative.** These are atelier-on-atelier numbers; no LongMemEval,
  no other product measured under the same harness. They qualify the
  *lexical-vs-hybrid* claim only, not an "atelier beats X" claim — we make none.
- **n=1 vault, 22 paraphrase probes.** Small, personal, Korean+English mixed.
- **The label can lie; we caught it lying.** The first measurement produced
  identical numbers under both engines: a full reindex had silently orphaned
  the vector sidecar (chunk-id churn), so "hybrid" was lexical wearing the
  label — the engine canary proves the provider answers, not that semantic
  candidates survive the join (issue #114). The numbers above were taken
  *after* re-syncing and verifying the join (knn 40/40 → main DB). Any future
  re-measurement should verify the join first.
