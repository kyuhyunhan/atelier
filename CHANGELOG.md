# Changelog

All notable changes to atelier.

## [Unreleased]

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
