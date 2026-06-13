# atelier Schema (v4 → v5, RFC 0003)

Schema v4 was the first version under atelier's authority — a non-breaking evolution of
gorae's Schema v3. **RFC 0003 then evolved it to v5** (provenance/sensitivity as
first-class fields, the `raw/`→`provenance/` and `wiki/`→`graph/` renames, learnings
relocated under `provenance/learning/`, digests/synthesis retired to query-time, themes
folded into `domain` entities). This doc reflects the current (v5) state; the v3→v4 and
RFC 0003 sections below record the evolution. Canonical definition lives in
`schema/data/*.yaml`.

---

## Files

| File | Role |
|---|---|
| `schema/data/base.yaml` | Common frontmatter fields shared across all spaces |
| `schema/data/gorae.overlay.yaml` | gorae space: provenance sources + graph (entity) page types |
| `schema/data/workshop.overlay.yaml` | workshop space: products, notes, logs |
| `schema/data/learnings.overlay.yaml` | learnings domain: candidate / accepted / principle / archived |
| `schema/data/linking.yaml` | URI scheme, wikilink syntax, backward compat rules |
| `schema/data/lint.yaml` | L1–L7 rules as data (severity, automation, DB queries) |
| `schema/db/sql/0001_initial.sql` | SQLite DDL — tables, FTS5, views, indexes, triggers |

---

## v3 → v4 Changes

### What changed

| Item | v3 | v4 |
|---|---|---|
| `schema_version` field value | `3` | `4` |
| Schema authority | `gorae/SCHEMA.md` (inline) | `atelier/schema/data/*.yaml` |
| Lint rules | Prose in SCHEMA.md | Machine-readable `lint.yaml` |
| DB | None | SQLite (`~/.atelier/cache/atelier.db`) |
| Mobile fields | Not present | `source`, `inbox_status` in `base.yaml` (nullable, not validated until Phase H) |

### What did NOT change (v3 → v4)

- All v3 field names, types, and semantics are identical in v4.
- Web Clipper template works with `schema_version: 4` (same fields, bump only).

## v4 → v5 Changes (RFC 0003)

| Item | v4 | v5 (current) |
|---|---|---|
| Directories | `raw/`, `wiki/`, `learnings/` | `provenance/`, `graph/`, `provenance/learning/` |
| `provenance` field | — | `personal`\|`knowledge`\|`learning` (first-class, projected DB column) |
| `digest` / `synthesis` pages | stored | retired to query-time (`atelier_think`, GP5) |
| `theme` pages | separate `themes/` tree | folded into `graph/entities/` as `category: domain` (GP2; original `scope` preserved) |
| entity `category` | person/place/book/concept/organization | + `domain`; `first_mention` now optional |
| Lint L4 (first-mention) | active | retired (was digest-derived) |

Old prefixes (`raw/`, `wiki/`, `learnings/`) still resolve via the reindex aliasing
layer, so the rename is non-breaking for existing links.

### Migration

`atelier reindex --space gorae --full` auto-migrates v3 files:
1. Reads frontmatter, detects `schema_version: 3`.
2. Writes `schema_version: 4` back via the writeback layer.
3. Records the migration in `meta` table.

No raw content is altered. The bump is the only diff.

---

## Base Fields

Defined in `schema/data/base.yaml`. All spaces inherit these.

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | integer | yes | `4` or `5` |
| `provenance` | enum | — | `personal`\|`knowledge`\|`learning` (RFC 0003; projected DB column) |
| `entry_id` | UUID v5 | yes | Derived from `created_at[0].value` |
| `title` | string | yes | Nullable |
| `summary` | string | no | Nullable |
| `sensitivity` | enum | no | `private` \| `public`; default `private` |
| `created_at` | array | yes | `[{value, precision, timezone}]` |
| `collected_at` | array | no | Capture timestamps (mobile-ready) |
| `edited_at` | array | no | Edit timestamps |
| `embedded_assets` | array | no | R2 asset keys; populated by hook |
| `word_count` | integer | no | Populated by hook |
| `source` | string | no | Capture channel (mobile-ready, nullable) |
| `inbox_status` | enum | no | `pending`\|`processed`\|`archived` (mobile-ready) |

---

## Gorae Overlay (gorae space)

Defined in `schema/data/gorae.overlay.yaml`. Paths cover both the canonical
post-RFC-0003 prefix and the legacy one (for un-migrated vaults).

### Page types

| Type | Path (canonical / legacy) | Writer | Status |
|---|---|---|---|
| `raw_source` | `provenance/{personal,knowledge}/**` / `raw/**` | human | active |
| `source` | `graph/sources/*` / `wiki/sources/*` | librarian | active |
| `entity` | `graph/entities/*` / `wiki/entities/*` | librarian | active (incl. `category: domain` = folded themes) |
| `digest` | `graph/digests/YYYY-MM` | librarian | **defined, retired in vault (GP5)** |
| `theme` | `graph/themes/*` | librarian | **defined, folded into entities (GP2)** |
| `synthesis` | `graph/synthesis/*` | librarian | **defined, retired in vault (GP5)** |

Learnings live in a separate overlay (`learnings.overlay.yaml`): `learning_candidate`
(`provenance/learning/candidates/**`), `learning_accepted` (`provenance/learning/notes/**`),
`learning_principle` (`provenance/learning/principles/*`), `learning_archived`
(`provenance/learning/archived/**`).

### Entity thresholds (unchanged from v3)

| Category | Threshold |
|---|---|
| personal person | 2+ digests |
| domain person | 1+ |
| place | 2+ |
| book | 1+ |
| concept (personal) | 3+ |
| concept (domain) | 2+ |

---

## Workshop Overlay (workshop space)

Defined in `schema/data/workshop.overlay.yaml`.

| Type | Path | Writer |
|---|---|---|
| `product` | `products/*/README.md` | builder |
| `product_page` | `products/**/*.md` | builder |
| `note` | `notes/**/*.md` | builder |
| `log` | `logs/**/*.md` | builder |

---

## Lint Rules

Defined in `schema/data/lint.yaml`. Loaded by `runtime/lint/` at startup.

| ID | Name | Severity | Auto |
|---|---|---|---|
| L1 | broken-links | FAIL | yes |
| L2 | hallucination | FAIL | no (manual) |
| L3 | source-count | WARN | yes (fixable) |
| L4 | first-mention | — | **RETIRED (RFC 0003 GP5; was digest-derived)** |
| L5 | orphan | WARN | yes |
| L6 | stale | INFO | yes |
| L7 | gap | INFO | no (manual) |

`atelier lint` runs L1, L3, L5, L6 automatically.
`atelier lint --fix` applies the L3 fixer.
L2 and L7 are flagged only after manual agent review.

---

## DB Schema (Phase A)

Defined in `schema/db/sql/0001_initial.sql`. Stored at `~/.atelier/cache/atelier.db`.

| Table / Object | Purpose |
|---|---|
| `pages` | One row per indexed file; frontmatter as JSON |
| `chunks` | Paragraph-level text chunks for FTS |
| `chunks_fts` | FTS5 virtual table (unicode61 tokenizer) |
| `links` | All wikilinks extracted; `to_page_id` NULL = broken |
| `entities` | Canonical entity slugs + aliases |
| `meta` | `schema_version`, `atelier_db_version`, `created_at` |
| `backlinks_count` | View: inbound link counts per page |
| `broken_links` | View: links with no resolved target |

The DB is **derived and gitignored**. It can be rebuilt at any time with:

```
atelier reindex --space gorae --full
atelier reindex --space workshop --full
```

---

## URI Scheme

Defined in `schema/data/linking.yaml`.

```
[[gorae:graph/entities/foo.md]]         # qualified cross-space link
[[gorae:graph/entities/example.md|example]]      # with display label
[[workshop:products/bar/README.md]]     # workshop link
[[provenance/personal/diary/2026/01/01.md]]    # bare link (resolved as gorae:)
```

Bare and legacy-prefixed links (`[[raw/...]]`, `[[wiki/...]]`, `[[provenance/...]]`,
`[[graph/...]]`) are treated as `gorae:`-scoped during
indexing. The linker records the resolved form; source files are not rewritten until
`atelier promote apply` is run.
