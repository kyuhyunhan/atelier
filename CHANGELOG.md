# Changelog

All notable changes to atelier.

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
