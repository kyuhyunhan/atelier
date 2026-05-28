# Changelog

All notable changes to atelier.

## [0.2.2] — Dream cycle (automated principle synthesis) — IN PROGRESS

Design is documented first (doc-first); implementation lands across
PR-29..33. See *Learnings domain & dream cycle* in `docs/ARCHITECTURE.md`
for the full rationale.

### Design landed (PR-34, this entry)

- **`docs/ARCHITECTURE.md` — "Learnings domain & dream cycle"** section:
  three-tier model (candidates / accepted / principles), bidirectional
  capture↔inject flow, the dream cycle's cluster→synthesize→promote
  split, the **usage-coupled (not wall-clock) trigger** rationale for a
  laptop that sleeps, and the **interruption-resilience** rules
  (incremental durability, atomic writes, idempotent re-run vs.
  proposed+accepted+archived, self-healing `last_dream_at`,
  filesystem-as-checkpoint).

### Planned (not yet implemented)

- **PR-29** — `atelier_learning_cluster`: deterministic clustering by
  FTS-term overlap + cross-project spread; meta tracks `last_dream_at`
  and accepted-since count.
- **PR-30** — principle `status: proposed` tier; atomic draft writes;
  evidence-overlap idempotent dedup.
- **PR-31** — `atelier_principle_review_proposed` + approve/reject.
- **PR-32** — `session_bootstrap` threshold nudge (last_dream_at based,
  detects an interrupted dream).
- **PR-33** — `atelier dream` convenience CLI + orchestration docs.

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
