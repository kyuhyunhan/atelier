# atelier — Architecture

This document describes what atelier is once built. For execution order and
phase plan, see `PLAN_v0.1.md`.

---

## One-line

atelier is a **methodology layer for a sovereign personal memory system**:
public-distributable code that operates on private, user-owned content
repositories via a derived local index.

---

## Three Layers

```
┌────────────────────────────────────────────────────────────────┐
│  Layer 1: atelier (methodology)                       PUBLIC   │
│  ──────────────────────────────────────────────────────────    │
│  schema/, agents/, runtime/, scripts/                          │
│  github.com/<user>/atelier                                     │
│  Distributable. Culture-neutral. No PII.                       │
└────────────────────────────────────────────────────────────────┘
              │
              │ operates on (read/write through agents)
              ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 2: content (per-user)                          PRIVATE  │
│  ──────────────────────────────────────────────────────────    │
│  <librarian-space>   — raw sources + wiki   (Librarian's space)│
│  <builder-space>     — products + notes     (Builder's space)  │
│  github.com/<user>/<each>  (private)                           │
│  Cloudflare R2  (embedded assets)                              │
└────────────────────────────────────────────────────────────────┘
              │
              │ projected into
              ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 3: local cache + config                  PER-MACHINE   │
│  ──────────────────────────────────────────────────────────    │
│  ~/.atelier/cache/atelier.db    SQLite + FTS5 (derived)        │
│  ~/.atelier/config.yaml          per-machine settings          │
│  ~/.atelier/voices/*.md          user-private persona overlays │
│  ~/.atelier/secrets/.env         tokens (chmod 600)            │
│  ~/.atelier/pii_patterns.txt     pre-commit guard regex        │
│  Ephemeral. Regeneratable. Never committed.                    │
└────────────────────────────────────────────────────────────────┘
```

The key invariant: **markdown is the truth; the DB is a projection.** Layer 3
can be deleted at any time and rebuilt from Layer 2 via `atelier reindex`.

---

## Two Stewards

```
                     ┌────────────────────┐
                     │   atelier runtime  │
                     │  (service-shaped)  │
                     └─────────┬──────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
     ┌──────────────────┐              ┌──────────────────┐
     │    Librarian     │              │     Builder      │
     │  ─────────────   │              │  ─────────────   │
     │  WRITE: wiki/**  │              │  WRITE: workshop │
     │  READ:  raw/**   │              │  READ:  gorae/** │
     │  READ:  workshop │              │  READ:  raw/**   │
     │                  │              │                  │
     │  ops: ingest,    │              │  ops: new-prod,  │
     │       query,     │              │       log, adr,  │
     │       delete,    │              │       retro      │
     │       lint       │              │                  │
     └──────────────────┘              └──────────────────┘
              │                                 │
              │  promote propose  ←──────────────┘
              ▼
       (wiki updated by Librarian only)
```

Single-writer per space is the integrity invariant. Promotion from workshop
→ wiki always passes through Librarian via the promote pipeline.

> **Stewards are role labels, not runtime agents.** "Librarian" and "Builder"
> are *not* autonomous processes the engine spawns or loads — the engine never
> reads `agents/*.md`. Their only runtime teeth are the single-writer locks in
> `runtime/service/claims.py` (the `librarian-write` / `builder-write` roles).
> The contracts in `agents/` describe the *responsibilities and voice* of
> whoever fills a role — a human, a Claude session, or a user-authored skill —
> while `claims.py` enforces the one invariant that must hold no matter who
> writes: one writer per space. Procedural knowledge ("how to ingest", "how to
> log an ADR") is intended to live as user-authored **skills**, not baked into
> an engine agent.

---

## Data Flow

### Ingest (gorae)

```
human writes raw/personal/diary/2026/05/15.md
        │
        ▼
"ingest this diary"  ──▶  Librarian agent
        │
        ▼
1. read raw file
2. update or create wiki/digests/2026-05.md
3. extract entities → update wiki/entities/*.md
4. update wiki/themes/*.md
5. atelier reindex (rebuild DB tables for changed pages)
6. append wiki/log.md
7. git commit wiki/
```

### Query (cross-space)

```
"what did I learn about X this year?"
        │
        ▼
Librarian agent
        │
        ▼
1. atelier search "X" --space gorae --mode graph
   → SQLite FTS5 + links table BFS
2. drill down into ranked pages
3. (optional) cross-space search workshop for product context
4. synthesize answer
5. ask user: file as synthesis?
6. if yes → wiki/synthesis/*.md → reindex → log → commit
```

### Build (workshop)

```
atelier new-product foo
        │
        ▼
1. scaffold workshop/products/foo/README.md  status=active
2. Builder agent active in this product
3. work proceeds: spec/, adr/, retro/, log/
4. atelier reindex --space workshop
5. git commit
```

---

## Learnings domain & dream cycle

The `learnings/` subtree (added v0.2.1) is the **developer-self memory**:
lessons accumulated across *every* project the user touches through an
agent, then distilled into universal principles. It is the bidirectional
counterpart to the rest of the vault — where `raw/`+`wiki/` capture
*knowledge* and `workshop/` captures *product work*, `learnings/`
captures *how the developer works*.

### Three tiers

```
learnings/
├── candidates/<date>/<slug>.md     tier 1 — aggressive capture, append-only
│      ↓  (review: accept / archive)
├── accepted/
│   ├── by-topic/<topic>/<slug>.md  tier 2 — curated, canonical
│   └── by-project/<project>/<slug> tier 2 — same entries, project-indexed mirror
│      ↓  (dream cycle: cluster → synthesize)
└── principles/<slug>.md            tier 3 — cross-project ethos
       priority: always-inject | on-relevant-prompt | manual-only
```

- **tier 1 (candidates)** — captured by an *agent*, not a blind hook. A
  bash hook cannot judge what was learned or *why it matters*, so the
  capture path is gated: `capture(require_why=True)` rejects content-free
  stubs (`no-substance` / `empty-why`). Instead of firing empty captures,
  a `SessionStart` hook (`scripts/hooks/capture-disposition.sh`) plants a
  *capture disposition* into the model context so the live agent records
  durable lessons itself — with a real `why` — via `atelier_learning_capture`.
  `atelier_absorb_claude_memory` imports Claude Code's own per-project
  memory (`require_why=False`, since it carries free-form rationale).
  Signal/noise separation is still deferred to promotion.
- **tier 2 (accepted)** — promoted by a curator through
  `atelier_learning_accept`, gated by `criteria.yaml` (must/should/
  forbidden). The canonical copy lives under `by-topic/`; `by-project/<n>/`
  is a **generated view**, not a source — retrieval (recall, bootstrap §B)
  selects on the `target_project` *facet*, never the folder, and the whole
  `by-project/` tree is regenerable from canonical via the reconcile routine
  (delete it and `repair()` reproduces it). Project is a derived facet, not a
  placement decision.
- **tier 3 (principles)** — generalizations that hold across projects.
  `priority: always-inject` principles are surfaced at *every* session
  start; this is the highest-authority, highest-blast-radius tier, so it
  is gated hardest.

### Bidirectional flow

```
        ┌──────────────── Claude Code session ────────────────┐
        │  working_dir = ~/ws/<project>                         │
        └───────┬───────────────────────────────┬──────────────┘
   capture (←)  │                                │  inject (→)
                ▼                                ▼
   Stop/SessionEnd hook                 UserPromptSubmit hook
   → atelier_learning_capture           → session_bootstrap (1st turn):
   → candidates/                           always-inject principles +
                                            by-project/<project> learnings
   absorb (← offline)                   → signal_recall (every turn):
   → atelier_absorb_claude_memory          FTS-ranked learnings for this
   → accepted/ + candidates/               prompt, project-boosted
```

Capture and absorb feed the vault; bootstrap and recall feed the agent.
The loop closes: today's session deposits learnings that boost tomorrow's
session in any project.

Two visibility channels carry the injection: `additionalContext`
(session_bootstrap, signal_recall) is *model-only* — the agent sees it,
the user does not — while a `SessionStart` `systemMessage` hook
(`scripts/hooks/session-nudge.sh`) and the statusline wrapper
(`scripts/hooks/statusline-atelier.sh`, backed by `atelier dream --status`)
surface the dream nudge to the *user*.

### Dream cycle — automated principle synthesis

Manually authoring principles does not scale. The **dream cycle** automates
*discovery* and *drafting* while keeping the high-blast-radius
`always-inject` decision with a human. Work splits in three:

```
① cluster (mechanical)   engine — atelier_learning_cluster
   "which learnings form one group?"  FTS-term overlap + cross-project
                                       spread (≥2 projects)
        ▼
② synthesize (semantic)  agent — the live Claude session
   "generalize this group into one rule"  → atelier_principle_synthesize
                                              (status: proposed)
        ▼
③ promote (judgement)    human — atelier_principle_review_proposed
   "worth injecting into every session?"  → approve (→ accepted) / reject
```

The engine is domain-ignorant, so it cannot do ② — it only tees up ①
deterministically and guarantees safe writes. ② runs in whatever live
Claude session the user is in (no separate daemon, no LLM inside the
engine).

At ③ (and at ordinary candidate promotion), the rule-based `must`
criteria are a safety net against un-reviewed auto-accepts. A reviewed
curator may pass `override_must` to accept content whose free-form prose
trips a heuristic (e.g. a real rationale with no `## Why` header); the
`forbidden` gate (pii / pure-meta) is never overridable.

### Why usage-coupled, not scheduled

atelier runs on a laptop that sleeps when the lid closes. Wall-clock
schedulers (cron, launchd `StartCalendarInterval`) are unreliable there:
a 3am job never fires under a closed lid, and missed-run coalescing on
wake is fragile. The dream cycle is therefore **event-driven, not
time-driven**:

```
lid closed   → no new learnings accrue → no dream needed   (self-consistent)
lid open     → learnings accrue AND a Claude session is live → dream is both
               needed and runnable at the same moment
```

`session_bootstrap` checks a threshold (`accepted_since_last_dream ≥ N`
**or** `days_since_last_dream ≥ D`) and, when crossed, appends a one-line
nudge to the first-turn context. The user (or the agent) then runs the
dream pass *in that live session*. The cadence auto-matches usage; the
laptop's intermittent availability is a non-issue by construction.

> **Note on sleep vs. termination.** Closing the lid *suspends* the
> `atelier serve` process (frozen, memory + disk buffers preserved),
> it does not terminate it. The server resumes on wake. It only truly
> dies on logout/reboot, kill/crash, or (if not run under `nohup`) the
> launching terminal closing.

### Interruption resilience (lid closed mid-dream)

A dream pass may be interrupted at any point — the lid closes while the
agent is mid-synthesis. Two severities:

- **sleep** (lid close): gentle. Process frozen; no file corruption
  (not a power loss — OS buffers survive in RAM and flush on wake). Only
  in-flight *network* (Anthropic API, local MCP socket) may time out on
  wake → retryable.
- **reboot / power loss**: harsh. `atelier serve` is gone; an in-flight
  write could be truncated.

Both reduce to **partial completion**, not corruption, handled by five
rules:

1. **Incremental durability** — clusters are processed one at a time;
   each completed `cluster → proposed draft` is committed immediately.
   An interruption loses only the in-flight cluster, never prior work.
2. **Atomic writes** — each draft is written to `.<slug>.tmp` then
   `os.rename`d (atomic on POSIX), so even power loss leaves no half-file.
3. **Idempotent re-run** — a cluster is skipped if its member learnings
   already overlap (≥K) the `evidence` of an existing principle in
   **proposed *or* accepted *or* archived** state. Checking archived too
   means a cluster the user already *rejected* is never re-proposed.
4. **`last_dream_at` advances only on clean completion** — an interrupted
   pass leaves it stale, so the nudge re-fires and the next pass resumes
   (idempotently skipping done clusters). Self-healing; no resume logic.
5. **Checkpoint = filesystem** — the `proposed/` drafts themselves are
   the progress record. No separate state file to corrupt.

| interruption point | sleep | reboot |
|---|---|---|
| cluster 3/8 in progress | 3 saved; in-flight retried on wake or next dream | 3 saved (atomic); next dream resumes at #4 |
| mid `synthesize` write | buffer survives, completes on wake | only `.tmp` left → ignored, regenerated |
| just before agent reports | draft already on disk; only chat report lost | same |
| fully complete | last_dream_at set, nudge clears | same |

Every cell resolves to *data-safe + resumes on the next opportunity*.

---

## Component Map

```
runtime/
├── index/          File → DB pipeline
│   ├── crawl.py        walk filesystem, detect changes by mtime + content_hash
│   ├── parse.py        frontmatter + body → structured data
│   ├── linker.py       extract [[...]] → links table
│   ├── entities.py     entity extraction + canonical_slug assignment
│   ├── writeback.py    DB → markdown (for L3/L4 fixes, promote apply)
│   └── reindex.py      orchestrator
│
├── search/         Query DB → results
│   ├── fts.py          FTS5 ranking with snippet extraction
│   ├── graph.py        BFS over links table
│   └── render.py       result formatting
│
├── lint/           Apply rules from lint.yaml
│   ├── L1.py           broken-links
│   ├── L3.py           source-count
│   ├── L5.py           orphan
│   └── L6.py           stale
│
├── doctor/         Drift detection + remediation
│   ├── D1..D7.py       individual diagnostics   (D7 = learnings-mirror reconcile)
│   └── remediate.py    bounded fixer (--max-usd N for LLM cost cap)
│
├── sync/adapters/  Remote ↔ local
│   ├── github.py       git push/pull/status + commit/safety-predicates
│   ├── r2.py           Cloudflare R2 asset sync
│   └── local_fs.py     fallback / dry-run
│
├── promote/        workshop → wiki proposal pipeline
│   ├── propose.py      generate proposal document
│   └── apply.py        Librarian writes wiki page; backlink to source
│
├── service/        long-running engine + MCP surface
│   ├── server.py       `atelier serve` asyncio supervisor + pidfile guard
│   ├── vault_autosync.py  background poller: commit+push vault when dirty+quiescent
│   ├── tools.py        the single MCP tool registry (claim + role lock)
│   ├── mcp_stdio.py    MCP stdio transport (Claude Code subprocess)
│   ├── mcp_http.py     MCP HTTP transport (loopback + bearer)
│   ├── mcp_call.py     `atelier-mcp-call` — hooks call MCP from the shell
│   ├── api.py          shared funnel that CLI + MCP both call into
│   ├── auth.py         Session + bearer-token validation
│   ├── claims.py       capability claims + per-role asyncio write locks
│   ├── capture.py      raw-inbox capture (mobile-reserved)
│   ├── jobs/           youtube · clip · prepare · pending · index_regen · new_doc
│   └── learnings/      capture · review · principles · dream · cluster ·
│                       bootstrap · recall · absorb_claude · indexes ·
│                       criteria · reconcile   (by-project mirror drift check/repair)
│
└── util/           Cross-cutting
    ├── config.py       ~/.atelier/config.yaml loader
    ├── fs.py           safe file ops
    └── logging.py      structured logs
```

Every CLI command in `scripts/atelier` is a thin wrapper around a function in
`runtime/service/api.py`. This keeps the door open for MCP and HTTPS surfaces
in v0.2 without restructuring.

---

## Storage

### Filesystem (authoritative)

```
<librarian-space>/                    Librarian territory (path from config.yaml)
├── raw/                              human-written, immutable from agents
│   ├── personal/{diary,writings,...}
│   ├── knowledge/{...domains...}
│   └── personal/inbox/               (mobile inbox landing; v0.3)
├── wiki/                             Librarian-written
│   ├── digests/, sources/, entities/, themes/, synthesis/
│   ├── index.md                      auto-regenerated
│   └── log.md                        append-only

<builder-space>/                      Builder territory (path from config.yaml)
├── products/{name}/
│   ├── README.md
│   ├── spec/, adr/, retro/, log/
├── notes/
└── logs/
```

### SQLite (derived)

`~/.atelier/cache/atelier.db` — see `schema/db/sql/0001_initial.sql`:

- **pages** — one row per markdown file (slug, space, page_type, frontmatter JSON, content_hash, mtime, generated cols)
- **chunks** — paragraph-level text for FTS
- **chunks_fts** — FTS5 virtual table, unicode61 tokenizer (Korean fuzzy via LIKE fallback in Phase A)
- **links** — every `[[...]]` extracted; `to_page_id = NULL` means broken
- **entities** — canonical slugs + alias JSON
- **meta** — schema_version, atelier_db_version, etc.
- **view backlinks_count**, **view broken_links**

The DB is rebuilt deterministically: same markdown → same DB. This makes
sync, multi-machine, and "rm -rf cache && reindex" all safe.

### R2 (assets)

Embedded images and binaries live in Cloudflare R2 with the CDN URL stored
in the markdown. The `embedded_assets` frontmatter field tracks asset slugs
so `atelier sync` can detect drift between local cache and remote.

---

## Trust Boundary

The service layer (`runtime/service/`) enforces, as of v0.2:

- **Bearer auth on the MCP HTTP transport** (`auth.authenticate_bearer`):
  a static token from `~/.atelier/secrets/.env`, with the bind forced to
  loopback (`mcp_http._resolve_settings` refuses non-loopback).
- **Single-writer-per-role locks** (`claims.SpaceLockRegistry`): write
  tools acquire an `asyncio.Lock` keyed by writer role, and each tool
  declares the claim it requires (`tools.ToolDef.claim`).
- **Single-instance guard** (`server._acquire_pidfile`): a second
  `atelier serve` fails fast instead of colliding on the port.

Deferred to v0.3: OAuth for the HTTP transport (loopback + static bearer
is sufficient for the single-user, single-machine case today).

---

## Vault auto-sync

`service/vault_autosync.py` is a **background subsystem** (registered with
the supervisor via `server.register_background`, not a transport). When
`vault.auto_commit.enabled` is set, it persists the vault to its git remote
with zero manual effort — completing the other half of the sync loop the
engine already had (`pull → reindex`).

**Why observer-side, not producer-side.** The trigger watches the vault's
git *working-tree state* on a fixed poll (default 30 s), not the writer.
This is the only design that catches **both** ingest paths — atelier's own
MCP write tools *and* direct edits (an editor, a script, another agent) —
because it never asks "who wrote this," only "is the tree dirty."

**Quiescence, not a watcher.** A burst of writes is coalesced into one
commit by committing only when `git status --porcelain` is *unchanged
across two consecutive polls* (`require_stable`). This needs no filesystem
watcher, no thread/loop bridge, and no debounce timer — the poll interval
*is* the settle window. The per-tick decision is the pure function
`_decide()`; blocking git runs in `asyncio.to_thread` so the poller never
stalls the transports sharing its loop.

**Safety gates** (all must pass before a commit, in `orchestrator.commit_push`
+ the poller): repo is the vault toplevel (guards a repo-wide `add -A` when
the vault is nested), not mid merge/rebase/cherry-pick and no `index.lock`
(never clobber a human's in-progress git op), and no writer-role lock held
(don't commit mid tool-write). `git add -A -- .` then commit only if
something is actually staged.

**Conflict = surface, never reconcile.** On a non-fast-forward push the
poller logs and stops — it does **not** auto pull/merge/force. Auto-pull
would mutate markdown out from under the DB projection (reindex is
deliberately *not* wired into sync), so reconciliation stays an explicit
user action.

> ⚠️ **Two operational caveats** (also flagged in `config/example.config.yaml`):
> - **No PII guard on the vault remote.** The pre-commit PII guard is
>   installed only into the *engine* repo (`scripts/setup`). Auto-push ships
>   vault content to its remote within one poll interval with no scan — use
>   only with a **private** vault remote.
> - **Multi-device divergence.** Two machines auto-pushing the same vault
>   will diverge (the second push is rejected non-fast-forward). No data is
>   lost — local commits are kept and the divergence is surfaced — but
>   reconciliation is manual.

---

## Logging

All logging funnels into **one append-only file**: `~/.atelier/logs/atelier.log`
(override with `ATELIER_LOG_FILE`; configurable via the `logging:` block). Built
on stdlib `logging` (`runtime/util/logging.py`). Every line is structured and
always carries **time and category**:

```
2026-06-03T16:04:25+09:00 [INFO] [vault-autosync] ready vault=/…/gorae interval=30
```

- **Category = logger name.** The façade keeps `log.info("sync.commit", k=v)`;
  the first dotted segment (`sync`) becomes the logger `atelier.sync` →
  `[sync]`. No-dot messages fall under `[cli]`.
- **Append across restarts.** `configure()` opens a `FileHandler(mode="a")` and
  is idempotent — relaunching `atelier serve` appends, never truncates, and never
  duplicates handlers.
- **stdout is sacred.** No handler ever writes stdout (the stdio MCP transport
  owns it for JSON-RPC). The optional console handler is stderr-only and only
  attaches on an interactive TTY.
- **Library logs consolidated.** uvicorn and the `mcp`/FastMCP loggers are bridged
  to the same file (`[uvicorn]`, `[mcp]`).
- **Hooks share the format.** `mcp_call.py` and the bash hooks (via
  `scripts/hooks/_log.sh`) emit the byte-identical line shape to the same file.
- Resolution precedence — path: `ATELIER_LOG_FILE` > `logging.file` >
  `CACHE_DIR/../logs/atelier.log`; level: arg (`--verbose`) >
  `ATELIER_LOG_LEVEL` > `logging.level` > `info`. `logging` never raises from a
  log call (config is read lazily/defensively).

The `~/.atelier/logs/injected/<session>.md` files are **not logs** — they are
audit snapshots of context injected into each Claude session, left untouched.

---

## Mobile Reservation

The mobile channel is **out of scope for v0.1** but the architecture preserves
five named entry points:

| Reservation | Where | Active in |
|---|---|---|
| `base.yaml.source` and `inbox_status` | schema/data/base.yaml | Phase 1 (defined, nullable) |
| `raw/personal/inbox/` directory | gorae | Phase 9 (created on first capture) |
| `runtime/service/capture.py` | runtime | Phase 7 (function, no HTTP) |
| `claims.py` `mobile-claim` enum | runtime/service | Phase 7 (placeholder) |
| `config.channels.mobile` | example.config.yaml | Phase 0 (commented) |

v0.3 turns these on by adding an HTTPS endpoint and a mobile client. No
schema or DB migration is required at that time.

---

## Delivered since v0.1

| | Landed | Note |
|---|---|---|
| MCP stdio + HTTP exposure | v0.2.0 | `atelier serve`, bearer + loopback |
| Real trust-boundary enforcement | v0.2.0 | bearer auth + per-role asyncio locks |
| Dream cycle (automated principle synthesis) | v0.2.2 | usage-coupled, *not* cron-driven (laptop sleeps) |

## Still out of scope

| | Future | Why deferred |
|---|---|---|
| OAuth for the HTTP transport | v0.3 | loopback + static bearer suffices for single user |
| Active mobile capture | v0.3 | scaffolding only (see Mobile Reservation) |
| Hybrid search (vector + BM25 + RRF), sqlite-vec / embeddings | v0.3 | FTS5 baseline first |
| LLM facets reclass on `prepare_commit`; OpenAI STT on YouTube ingest | v0.3 | needs a runtime LLM gateway / credentials |
| L2 automated hallucination lint | v0.3 | LLM-dependent |
| Korean trigram tokenizer | v0.3 | unicode61 baseline first |
| launchd / autostart for `atelier serve` | v0.3 | foreground-only by choice |
| Automatic acceptance-criteria scoring (LLM) | v0.3 | human-in-the-loop review only today |
| Discord transport | — | out of scope by decision |
| Multi-user federation | v1.x | not in mandate |
