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
│   ├── D1..D6.py       individual diagnostics
│   └── remediate.py    bounded fixer (--max-usd N for LLM cost cap)
│
├── sync/adapters/  Remote ↔ local
│   ├── github.py       git push/pull/status
│   ├── r2.py           Cloudflare R2 asset sync
│   └── local_fs.py     fallback / dry-run
│
├── promote/        workshop → wiki proposal pipeline
│   ├── propose.py      generate proposal document
│   └── apply.py        Librarian writes wiki page; backlink to source
│
├── service/        Server-shaped API surface
│   ├── api.py          all CLI commands call into here
│   ├── auth.py         token validation (placeholder until v0.2)
│   ├── claims.py       capability claims (mobile-claim etc.)
│   └── capture.py      mobile/external capture endpoint
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

## Trust Boundary (v0.1: placeholder)

The service-shape layer (`runtime/service/`) has stubs for `auth.py` and
`claims.py` but performs no real enforcement in v0.1 (single user, single
client). The shape exists so v0.2 can add MCP/HTTPS exposure without
restructuring callers.

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

## Out of scope for v0.1

| | Future | Why deferred |
|---|---|---|
| MCP stdio/HTTPS exposure | v0.2 | service-shape needs to stabilize first |
| Active mobile capture | v0.3 | scaffolding only in v0.1 |
| Hybrid search (vector + BM25 + RRF) | v0.3 | FTS5 baseline first |
| sqlite-vec / embeddings | v0.3 | same |
| Dream cycle (cron-driven curation) | v0.2 | operational stability first |
| L2 automated hallucination lint | v0.2 | LLM-dependent |
| Korean trigram tokenizer | v0.2 | unicode61 baseline first |
| Public atelier | v0.3+ | review private operation first |
| Multi-user federation | v1.x | not in v0.1 mandate |
| Real trust-boundary enforcement | v0.2 | single-client in v0.1 |
