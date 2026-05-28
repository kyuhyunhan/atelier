# atelier Schema v4

Schema v4 is the first version under atelier's authority. It is a non-breaking evolution
of gorae's Schema v3: all field meanings are preserved; the version number bumps to signal
that atelier (not gorae's SCHEMA.md alone) is the canonical definition point.

---

## Files

| File | Role |
|---|---|
| `schema/data/base.yaml` | Common frontmatter fields shared across all spaces |
| `schema/data/librarian.overlay.yaml` | gorae space: raw sources + wiki page types |
| `schema/data/builder.overlay.yaml` | workshop space: products, notes, logs |
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

### What did NOT change

- All v3 field names, types, and semantics are identical in v4.
- gorae directory structure is unchanged (`raw/`, `wiki/`, SCHEMA.md).
- All 5 wiki page types (digest, source, entity, theme, synthesis) are unchanged.
- Lint rules L1–L7 have the same definitions; L2 and L7 remain manual.
- Web Clipper template works with `schema_version: 4` (same fields, bump only).

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
| `schema_version` | integer | yes | Must be `4` |
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

## Librarian Overlay (gorae space)

Defined in `schema/data/librarian.overlay.yaml`.

### Page types

| Type | Path | Writer |
|---|---|---|
| `raw_source` | `raw/**/*.md` | human |
| `digest` | `wiki/digests/YYYY-MM.md` | librarian |
| `source` | `wiki/sources/*.md` | librarian |
| `entity` | `wiki/entities/*.md` | librarian |
| `theme` | `wiki/themes/*.md` | librarian |
| `synthesis` | `wiki/synthesis/*.md` | librarian |

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

## Builder Overlay (workshop space)

Defined in `schema/data/builder.overlay.yaml`.

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
| L4 | first-mention | WARN | yes (fixable) |
| L5 | orphan | WARN | yes |
| L6 | stale | INFO | yes |
| L7 | gap | INFO | no (manual) |

`atelier lint` runs L1, L3, L4, L5, L6 automatically.
`atelier lint --fix` applies L3, L4 fixers.
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
[[gorae:wiki/entities/foo.md]]          # qualified cross-space link
[[gorae:wiki/themes/example.md|example theme]]   # with display label
[[workshop:products/bar/README.md]]     # workshop link
[[raw/personal/diary/2026/01/01.md]]    # v3 bare link (resolved as gorae:)
```

v3 bare links (`[[raw/...]]`, `[[wiki/...]]`) are treated as `gorae:`-scoped during
indexing. The linker records the resolved form; source files are not rewritten until
`atelier promote apply` is run.
