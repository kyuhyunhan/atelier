# RFC 0006 — Memory north-star: one graph, always-fresh projection, consumer-scoped lenses, curated recall

| | |
|---|---|
| **Status** | Draft (proposed 2026-07-03) |
| **Scope** | the memory system as a whole — the truth→projection change feed; the MCP serving boundary; retrieval curation; data topology and self-description. This RFC is an **umbrella**: it sets goals, invariants, and a rubric-gated verification protocol; each pillar ships under its own follow-up RFC/phase. |
| **Builds on** | RFC 0005 (atomic graph, single-sourced structure), RFC 0002 (hybrid retrieval — **landed**), RFC 0001 (facets-not-paths) |
| **Reference** | `runtime/service/learnings/eval.py` (metric harness), `runtime/service/learnings/surfacing.py` (omission gate), `docs/rfc/0002-baseline.json` (frozen retrieval baseline) |
| **Schema** | no node-schema change in the foundation phase; pillar ① introduces a vault manifest + lens vocabulary in `schema/data/` |

---

## 1. Summary & thesis

The memory system works, but two faults are *felt* in daily use:

1. **The architecture is half a CQRS.** Markdown is the write model, SQLite the
   read model — but there is **no change feed between them**. Every read serves
   the *last `reindex`*, not the last write. `crawl.py` is per-file
   change-detected, yet the smallest *public* reindex unit is a whole space, and
   nothing runs it automatically after a write.
2. **Recall is one undifferentiated firehose.** A Claude Code coding session and
   a (future) personal agent query the *same* MCP surface with no notion of *who
   is asking*, so personal/knowledge data surfaces where a dev session has no use
   for it. The category the user wants already exists in the data (`domain`), but
   **nobody at the serving boundary uses it**.

> **Thesis.** The two "memory categories" are a **query-time lens, not a storage
> boundary** — one graph, many scoped views, cross-domain joins always possible.
> And the truth→projection relationship needs a **change feed** so reads never lag
> writes. Fix both behind a **rubric-gated, snapshot-verified** process: define the
> bar *before* the work, freeze a *before* baseline, and let an **independent
> agent** decide pass/fail.

This is the same move the codebase already made once at a smaller scale: RFC 0001
took learning classification out of *folders* and into *facets* ("classification
is a field, not a placement decision"). This RFC applies the identical principle
one level up: **audience is a query-time filter, not a storage split.**

## 2. Current state (the faults, observed)

The complete issue inventory, mapped to the pillar and phase that addresses each:

| # | Fault | Pillar | Evidence |
|---|---|---|---|
| 1 | No change feed truth→projection; reads serve last reindex | ② Fresh | `reindex_space` is the smallest public unit; no `reindex_path` in `runtime/` |
| 2 | Retrieval misses (lexical-only) | **mostly SOLVED** (④b / RFC 0002) | `0002-baseline.json` `p3_hybrid`: `dark_count 26→0`, concept `R@5 0.588→0.963` |
| 3 | No forgetting/consolidation; the pool only grows | ④a Curated | `lateral.py` merge-flagging is flag-only; `ac_status: passed` is terminal |
| 4 | No consumer scoping at the MCP boundary | ③ Scoped | one tool surface; recall has no audience parameter |
| 5 | Accreted topology; no vault self-description | ① Grounded (descriptive half) | no manifest; era inferred from which dirs exist (`resolver.py` carries both prefixes) |
| 6 | Coarse cache — routing fields inside a JSON blob | ② Fresh | only `title/sensitivity/provenance/created` are generated cols (`0001_initial.sql`) |
| 7 | Multi-machine divergence | **non-goal** | single-machine assumption (see §9) |

**On #2 — an honest correction.** Hybrid retrieval (semantic + lexical, RRF-fused)
**already landed** under RFC 0002 P3 (`engine='hybrid'`), extended by RFC 0003 P4
(the relational nudge — note `0002-baseline.json`'s key is `p4_relational_rfc0003`,
so P4 is RFC 0003 work, not 0002). The residual "misses" risk is not a missing
feature but an **operational** one: under `ATELIER_EMBED=off` the engine silently
degrades to `lexical-rrf`. The foundation baseline (§5) records the *live* engine
label, turning "are embeddings on in production?" from an assumption into a
measured fact.

## 3. Target model — one graph, scoped lenses

```
        NORTH STAR: one self-describing vault, one always-fresh projection,
        served through consumer-scoped lenses, memory that consolidates.

  Claude Code session ──▶ [dev lens]    default: operational (+knowledge on ask)
  (future) personal   ──▶ [life lens]   personal, knowledge
  you, directly       ──▶ [full lens]   everything; cross-domain joins
                              │
                              ▼
                 ┌────────────────────────────┐
                 │  ONE vault, ONE graph        │  no wall in storage;
                 │  domain is a FIELD           │  a lens is a DEFAULT FILTER,
                 │  freshness via a change feed │  never a hard boundary
                 └────────────────────────────┘
```

Four pillars realize it (details §7):

1. **Grounded** — the vault self-describes (a manifest); one **lens vocabulary**
   defined in `schema/data/` (data, not code — hard rule #3).
2. **Fresh** — a change feed: write-through per-file reindex + the autosync poller
   as a backstop for human edits; routing fields become indexed columns.
3. **Scoped** — lenses at the MCP boundary; a coding session defaults to the dev
   lens; the full lens still joins across domains.
4. **Curated** — forgetting/consolidation (④a); hybrid retrieval is already live
   (④b, RFC 0002); a future P5 reranker is out of this RFC's scope.

## 4. The rubric framework

The program is falsifiable: each pillar declares a **goal**, a **metric** (reusing
existing tooling wherever possible), and a **gate** an independent verifier checks.

### 4.1 Global invariants — checked at every pillar (the never-break bar)

| ID | Invariant | Metric | Bar |
|---|---|---|---|
| INV-1 | No data loss | every pre-existing `entry_id` still resolves, or has a recorded intended migration | 100% accounted |
| INV-2 | Rebuildable | `rm cache && reindex` reproduces the DB | deterministic |
| INV-3 | Truth-direction preserved | no write path makes the DB a sole source (markdown → DB only) | code-review pass |
| INV-4 | No silent omission | `eval.gate(before, after)` (`surfacing.diff`) | `newly_dark == []` |

### 4.2 Per-pillar goal → metric → gate

| Pillar | Goal | Metric (tool) | Gate |
|---|---|---|---|
| ① Grounded | vault self-describes; ONE lens vocabulary | manifest validates against schema; every `(kind, domain)` pair maps to exactly one lens; count of hardcoded lens lists in code | 0 hardcoded lens lists; resolver reads the lens map from `schema/data/` |
| ② Fresh | projection reflects a write without a manual reindex | staleness window (write→queryable); files reprocessed per engine write; **incremental-vs-full DB parity** (`reindex --full` DB == incremental DB) | read reflects any engine write with 0 manual reindex; parity test green; routing fields (`kind/domain/ac_status/surfacing`) are indexed columns |
| ③ Scoped | dev lens excludes personal; full lens still joins | dev-lens recall returns **0 `personal`-domain source/entity nodes**; operational R@k ≥ frozen baseline (`eval.py`'s existing `self_probe`/`concept_grouped` blocks already run over accepted operational claims — that *is* the operational R@k); a cross-domain "no-wall" probe still returns a join | all three hold simultaneously |
| ④a Forgetting | pool consolidates without omission | near-duplicate cluster count trend; `eval.gate` clean after any merge/retire | every merge/retire is human-gated **and** snapshot-diffed; `newly_dark == []` |
| ④b Hybrid (RFC 0002, landed) | semantic recall ≥ lexical | `paraphrase_block` R@k + MRR with `engine=hybrid` | never below the frozen lexical baseline on any probe set (regression guard only — feature already shipped) |

## 5. The two snapshots (kept separate on purpose)

The user's ask bundled two needs that want *different* mechanisms. Conflating them
yields a snapshot bad at both.

- **Data-safety snapshot — a rollback artifact, never diffed.**
  `git tag` the vault at a known-good commit **+** `tar` the untracked
  `~/.atelier/` durables (`config.yaml`, `voices/`, `secrets/`, `pii_patterns.txt`)
  into `~/.atelier/snapshots/<ts>/`. Rollback = restore tag + tar. Out-of-tree; it
  is *state*, not methodology.

- **Verification baseline — a comparison artifact, never restored.**
  `eval.run()` + a `surfacing.snapshot()` **aggregate** (`total`/`visible`/
  `dark_count` — not the full per-entry map, which is noisy and non-diffable) + a
  node **census**, frozen as `docs/rfc/0006-baseline.json` (mirrors
  `0002-baseline.json`). Committed and git-visible. The verifier re-runs the
  after-state and diffs against it.

  The census is **partitioned by `kind`**, not flat, because the routing fields
  are class-specific: `ac_status`/`surfacing` exist only on claims
  (`graph.overlay.yaml:120,131`); `domain` is an enum on sources/entities but a
  free string on claims. A flat "counts by `domain/kind/ac_status/surfacing`"
  would produce mostly-null buckets and make the §11.1 parity assertion
  ill-defined. Skeleton:

  ```json
  {
    "_about": "RFC 0006 P0 foundation baseline (read-only).",
    "captured_date": "YYYY-MM-DD",
    "engine": "hybrid | lexical-rrf",   // the LIVE label — records embeddings on/off
    "eval": { "...": "verbatim eval.run() output" },
    "surfacing": { "total": 0, "visible": 0, "dark_count": 0 },
    "census": {
      "claim":  { "domain": {"operational": 0}, "ac_status": {"passed": 0, "pending": 0},
                  "surfacing": {"query": 0, "proactive": 0, "always": 0} },
      "source": { "domain": {"personal": 0, "knowledge": 0, "inbox": 0, "workshop": 0} },
      "entity": { "in_scheme": {"personal": 0, "knowledge": 0, "inbox": 0, "workshop": 0} }
    }
  }
  ```

## 6. The workflow harness — independent verification

Each pillar runs as a deterministic three-stage workflow:

```
① snapshot   (freeze data-safety tag + confirm baseline is clean)
      ▼
② implement  (the change — agent or human; the ONLY stage that writes code)
      ▼
③ verify     (a SEPARATE agent, handed only {goal, rubric, frozen baseline};
              re-runs eval + census, applies INV-1..4 + the pillar gate,
              returns PASS/FAIL — never grades work it authored)
```

The verifier's independence is the point: the builder cannot both make the change
and rule it correct. The verifier refuses to run if the baseline file is dirty or
newer than the tag (guards against regenerating the "before" *after* the change).

## 7. The pillars (scope sketch — each ships under its own phase/RFC)

- **① Grounded.** New `.atelier-vault.yaml` manifest (structure version, vault id,
  declared lens map). Define the **lens vocabulary** in `schema/data/`, reconciling
  today's split: `domain` is an enforced enum `{personal,knowledge,inbox,workshop}`
  on sources (`graph.overlay.yaml:42`) and entities (`in_scheme`, `:89`) but a
  free-string `operational` on claims (`:122`). Because the value spaces are
  disjoint across node classes, **the lens map keys on `(kind, domain)`, not
  `domain` alone** — e.g. the dev lens selects `(claim, operational)` **and**
  `(source, knowledge)`, two different domain values on two classes. The map must
  cover both without breaking `recall.py:192`'s `domain == "operational"` predicate
  (a compat shim ships before ③ filters on it).
- **② Fresh.** Add a `reindex_path`/`reindex_file` entry reusing `reindex_space`'s
  upsert pass; call it write-through from the engine write paths; wire the autosync
  poller (`vault_autosync.py`) as the backstop for human edits. Promote
  `kind/domain/ac_status/surfacing` to indexed generated columns (issue #6).
  Decide the link/facet-rebuild scope for single-file reindex (today links rebuild
  per-space — a real design point, not a free lunch).
- **③ Scoped.** Add an audience/lens parameter to the recall/search MCP surface;
  default the Claude Code tools to the dev lens; keep a full lens for cross-domain
  work. Filter using the `(kind, domain)` lens map from ① against the indexed
  `kind` + `domain` columns from ② — a join over both classes, not a single-column
  `domain` filter (the split in ① makes a one-column filter incorrect).
- **④ Curated.** ④a: graduate `lateral.py` merge-flagging to a gated, snapshot-diffed
  apply; add a demotion/forget signal (e.g. a learning dark N audits running gets
  teed up for archive). ④b: hybrid is live — this pillar only guards against
  regression and confirms embeddings are on in the live runtime.

## 8. Migration & sequencing

This RFC's own delivery is **Foundation only** (P0). Pillars are separate, later,
each gated by §4.

```
P0  FOUNDATION (this RFC)
    P0.1  RFC 0006 doc                         [gate: user approves north star + rubrics]
    P0.2  census + baseline gen + snapshot     [gate: 0006-baseline.json committed;
          tooling                                     `atelier snapshot` restorable;
                                                       census parity + cold-DB tests green]
    P0.3  independent-verifier harness +        [gate: verifier PASS on unchanged vault
          workflow template                            (baseline==after); suite green]

P1  Pillar ① Grounded    [gate: §4.2 ① gate + INV-1..4]      ← default next
P2  Pillar ② Fresh       [gate: §4.2 ② gate + INV-1..4]
P3  Pillar ③ Scoped      [gate: §4.2 ③ gate + INV-1..4]
P4  Pillar ④ Curated     [gate: §4.2 ④ gate + INV-1..4]
```

Rule (inherited from RFC 0005): each phase = its own commit(s), green boundary, no
next phase before its gate passes. CHANGELOG records each phase as it ships
("RFC 0006 P0.x"); ARCHITECTURE.md back-annotates.

**Sequencing rationale.** Fresh (②) precedes Scoped (③): a scoped lens over a stale
projection still serves stale results. Grounded (①) precedes both: ②'s columns and
③'s lens both need the lens vocabulary ① defines. ④ is last and mostly ④a. *(If the
noise pain dominates, ③ may be pulled earlier at the cost of stale-projection
serving until ② lands — a deliberate, reversible call.)*

## 9. Non-goals (explicit)

- **Multi-machine / multi-device sync (#7).** Single-machine assumption. Auto-push
  stays as a backup; reconciliation stays manual. Not because it is hard — because
  the user runs one machine.
- **Per-vault cache DB.** One global `~/.atelier/cache/atelier.db` is correct for one
  vault; revisit only if a true multi-vault model lands.
- **Physical relocation of the vault tree / migration-scar cleanup.** ① takes only
  the *descriptive* half (manifest + lens vocabulary). Physically moving directories
  is the riskiest, least-reversible work and blocks nothing in ②③④; it is a later,
  separately-gated phase.
- **P5 reranker / weighted fusion.** A future retrieval-quality step (noted in
  `0002-baseline.json`), out of this RFC.
- **Vault split into separate personal/coding repos.** Explicitly rejected: one
  vault, lenses over walls.

## 10. Risks & mitigations

- **Lens vocabulary is load-bearing.** Because `domain` isn't uniform (`operational`
  free-string on claims vs enum on sources), a wrong lens map makes ③ gate on a
  moving target. *Mitigation:* ① defines the map in `schema/data/` with a compat
  shim before ③ filters on it; the map is data, single-sourced.
- **Baseline integrity.** A baseline regenerated *after* a change makes the diff
  meaningless. *Mitigation:* the baseline is committed and the verifier refuses to
  run against a dirty/newer baseline (§6).
- **`eval` engine label under CI.** `ATELIER_EMBED=off` reports `lexical-rrf`, so
  ④b's "hybrid ≥ lexical" gate must run with embeddings ON. *Mitigation:* document
  the required env for that one gate; the foundation baseline records the live label.
- **Single-file reindex link scope.** Links/facets rebuild per-space today; a naive
  per-file reindex could leave cross-file links stale. *Mitigation:* ② decides
  link-rebuild scope explicitly and the parity gate (incremental == full) catches
  divergence.

## 11. Verification (acceptance)

Foundation (P0) is accepted when:

1. **Census parity** — `census()` equals a filesystem tally on a freshly reindexed
   temp vault; cold-DB fallback returns identical numbers (mirrors
   `tests/test_projection_counts.py`).
2. **Baseline reproducibility** — regenerating `0006-baseline.json` on an unchanged
   vault yields identical metrics, **at a fixed embedding env** (the `engine` label
   and paraphrase scores depend on `ATELIER_EMBED`; the guarantee holds per-engine,
   as `0002-baseline.json` assumes live embeddings for its paraphrase block).
3. **Verifier no-op PASS** — `verify_against(baseline, <any rubric>)` on the
   unchanged vault returns PASS: `newly_dark == []`, every INV green, no metric
   below baseline.
4. **Snapshot rollback** — `atelier snapshot`, then a scratch mutation, then restore
   returns a temp vault (+ temp `~/.atelier`) to the tagged state.
5. **Full suite** green (`ATELIER_EMBED=off python3 -m pytest -q`).

Each pillar (P1–P4) is accepted when its §4.2 gate and INV-1..4 pass under the
independent verifier, with the data-safety snapshot taken first.
