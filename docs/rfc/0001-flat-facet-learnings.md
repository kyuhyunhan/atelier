# RFC 0001 — Flat, facet-based learnings memory

| | |
|---|---|
| **Status** | Proposed |
| **Scope** | `learnings/` domain; workshop retirement; persona removal |
| **Schema** | v4 → v5 (additive + one demotion, auto-migrated) |
| **Supersedes** | the `by-topic` / `by-project` mirror-tree layout |

---

## 1. Summary & thesis

**The filesystem is not a type system.** atelier's `learnings/` domain currently
encodes *classification* in *directory structure*. That substrate is the root of three
seemingly separate problems, which are one problem viewed from three sides:

1. a single field (`target_topic`) forced to mean two different things,
2. directories that cannot express the many-membership a lesson naturally has, and
3. "agent" personas whose only real identity was *which folder they wrote to*.

This RFC moves **all** classification out of the path and into **frontmatter facets**
that are **indexed and resolved at query time**. Learnings become a flat store; the
resolver — not the folder tree — answers "which project? which aspect? which topic?".
Once classification is reorganizable without moving files, the workshop space folds into
the unified store under one lock, and the personas have nothing left to attach to.

## 2. Current state (the problem, concretely)

### 2.1 Two trees, one duplicated

Accepted learnings live in a **canonical** topic tree plus a **duplicated** project mirror:

```
learnings/accepted/by-topic/<topic>/<slug>.md     ← canonical
learnings/accepted/by-project/<project>/<slug>.md  ← physical copy (shutil.copy2)
```

The mirror is written in `runtime/service/learnings/review.py` `accept()`
(~L211–309). It exists *only* because a folder is single-membership: to file a note
under both its topic and its project, the file is copied. Every reader already treats
`by-topic` as truth and **skips** `by-project` (`recall._fs_scan` L129;
`search._grep_walk` L62–64; `bootstrap._scan_accepted` L99–115 reads by-topic only;
`surfacing._enumerate_accepted` L55–87 reads by-topic only). An entire module
(`reconcile.py`) plus the **D7** doctor diagnostic (`runtime/doctor/diagnostics.py`
L137–158) exist solely to detect and repair drift between the two trees.

### 2.2 One field, two jobs — the `target_topic` fusion

`learning_accepted` requires `target_topic` and allows `target_project`
(`schema/data/learnings.overlay.yaml:54–65`). `target_topic` is meant to be a **global,
cross-project** axis (so a "git-workflow" lesson from any project clusters with others).
But the workshop→learnings absorb
(`scripts/absorb_workshop_memory_to_learnings/absorb.py:100–146`) mapped lexio's
**project-local** `layer` (client / server / cross-cutting / product) straight onto the
**global** `target_topic` (L123). The result:

```
lexio/cross-cutting    ┐
atelier/cross-cutting  ├──► all collapse into  by-topic/cross-cutting/
pmi/cross-cutting      ┘    (project boundary erased — "indiscriminate knowledge")
```

~100 lexio records carry a layer name in `target_topic`. The same absorb also dropped
lexio's `also_in` (secondary categories) and its typed `links:[{to, why}]`
(rationale-carrying edges) — strictly richer structure than learnings' bare
`links: [string]`.

### 2.3 Facets are not indexed

The DB stores frontmatter as a single JSON blob with only three generated columns
(`title`, `sensitivity`, `created`) in `schema/db/sql/0001_initial.sql:9–25`. There is
**no** column or index for `target_topic` / `target_project` / `observation_kind`.
Facet filtering happens in Python after loading the blob (`search.py:92–159`;
`recall._boost` L177–196). FTS (`chunks_fts`, L36–41) indexes **body text only** — never
frontmatter. So "find by classification" today depends on either the directory or a
post-hoc Python scan, never an indexed query. (This is the exact mechanism behind the
prior dark-learnings finding: a `touches` tag with no body echo is inert because FTS
can't see frontmatter.)

### 2.4 Personas are folder identity

`librarian` writes `wiki/`; `builder` writes `workshop/`. `ARCHITECTURE.md` already
notes these are role labels, not runtime processes — the engine never reads
`agents/*.md`. Their only teeth are write-locks in `claims.py`. In config, both
`librarian-territory` and `builder-territory` resolve to the **same vault root**
(`runtime/util/config.py`). The persona is `directory → who-writes` baked into a name.

## 3. Target model

### 3.1 Flat store, sharded by immutable time only

```
learnings/
├── candidates/<date>/<slug>.md     (unchanged — capture inbox)
├── notes/<YYYY-MM>/<id>.md         (NEW — flat accepted store)
├── archived/<slug>.md              (unchanged)
└── principles/<slug>.md            (unchanged)
```

`<YYYY-MM>` is derived from `captured_at` (immutable creation time, **not**
`accepted_at` which can change on re-accept). Date is *not* classification — it never
needs reorganizing — so it keeps the directory humane without putting meaning in the
path. `<id>` derives from the stable `entry_id`.

### 3.2 Classification = facets, resolved at query time

Every accepted note carries:

| Facet | Cardinality | Axis | Role |
|---|---|---|---|
| `target_project` | single | **project-local** | which project this came from |
| `aspect` | **many-valued** | **project-local** | which part(s): lexio→`[client, cross-cutting]`; pmi→its own vocabulary |
| `target_topic` | single, **optional** | **global** | cross-project clustering axis (dream) |
| `touches` | many-valued | global | concepts this is *about* (concept edges) |
| `links` | many-valued | — | typed `{to, why}` edges (adopted from lexio) |

The two project-local facets (`target_project`, `aspect`) and the global facet
(`target_topic`) are **never fused**. `aspect` is **free-form per project** — there is
no global aspect enum, because cross-project comparison is `target_topic`'s job. This is
the precise fix for §2.2.

### 3.3 The resolver

```
resolver = FTS(body, semantic)  +  facet filter (indexed, exact)

"lexio client-layer lessons"   → facet(project=lexio) ∧ facet(aspect=client)
"cross-project git-workflow"   → facet(topic=git-workflow)
"anything about auto-commit"   → FTS('auto-commit') ranked, then facet-narrowed
```

"The resolver classifies it" has a precise meaning: **facets are indexed columns the
resolver filters on**, not folders and not Python scans over a blob.

## 4. DB design

A single side table, populated at reindex, mirroring the proven concept-edge pattern
(`runtime/index/reindex.py:142–177` already projects `touches` + `target_topic` into the
`links` table deterministically, no LLM):

```sql
-- schema/db/sql/0002_learning_facets.sql
CREATE TABLE IF NOT EXISTS learning_facets (
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  kind    TEXT    NOT NULL,   -- 'project' | 'aspect' | 'topic' | 'touches'
  value   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lf_kind_value ON learning_facets(kind, value);
CREATE INDEX IF NOT EXISTS idx_lf_page       ON learning_facets(page_id);
```

**Why one table (option b), not generated columns (option a).** `aspect` is
many-valued; a generated column cannot hold an array for indexed equality, so option (a)
*still* needs a side table for `aspect[]` — and then runs two mechanisms in parallel.
One table gives a single filter predicate for every facet, single- or many-valued:

```sql
EXISTS (SELECT 1 FROM learning_facets lf
        WHERE lf.page_id = p.id AND lf.kind = ? AND lf.value = ?)
```

Population is **clear-and-repopulate per page** at reindex (DELETE this page's facet rows,
re-INSERT from frontmatter), exactly like the concept-edge code — so reindex stays
deterministic and idempotent (`same markdown → same DB`). Migrations are applied
lexicographically and idempotently (`runtime/util/db.py` `apply_migrations` L76–80);
`0002_*.sql` is picked up automatically with no version gate.

## 5. Schema changes

In `schema/data/learnings.overlay.yaml`, on `learning_candidate` and `learning_accepted`:

- **add** `aspect` → `{ type: array, items: { type: string } }` (many-valued, free-form).
- **adopt typed links**: `links` → `{ type: array, items: { type: object } }` with
  `{ to: string, why: string }`; migration backfills existing `links: [string]` to
  `[{to, why: ""}]`.
- **`also_in`** is accepted on ingest and **folded into `aspect`** (secondary values) at
  write time — it is not a separate stored field.
- **demote `target_topic`** on `learning_accepted` from `required_fields` → `optional_fields`
  (the only semi-breaking change: a flat, topic-optional store means many notes legitimately
  have no global topic).
- **`path_pattern`** for `learning_accepted`: `learnings/accepted/**/*.md` →
  `learnings/notes/**/*.md`.
- **`schema_version`** const `4` → `5`; add a v4→v5 entry to the base auto-migrate
  (`schema/data/base.yaml`) — additive, non-breaking apart from the documented demotion.

## 6. Persona retirement

**Write-locks** (`runtime/service/claims.py`) — rename, keep four distinct lock domains:

```
WriterRole / Claim
  librarian-write  →  wiki-write
  builder-write    →  learnings-write      (workshop folds here)
  captor-write     →  captor-write         (unchanged — candidate append)
  curator-write    →  curator-write        (unchanged — promotion/accept)
```

Captor and curator stay split: capture is cheap and frequent, curation is gated and
serialized; folding them would block captures behind curation.

**Re-key in lockstep** (one commit, or config validation rejects every subtree):
`config.py` `_VALID_WRITERS` + the `space_by_role("librarian-territory" /
"builder-territory")` synthesis (collapse to one `vault-write` accessor — both already
point at one root) across ~28 callers; `auth.py` default bearer claims; `tools.py`
claim/`lock_role` bindings (11 librarian-write tools, 1 builder-write).

**Delete** (no runtime consumer): `schema/data/librarian.overlay.yaml`,
`schema/data/builder.overlay.yaml`, `agents/librarian.md`, `agents/builder.md`, the
`agents: {librarian, builder}` voice-overlay config blocks and their doctor check;
`runtime/service/learnings/reconcile.py`, **D7** in `diagnostics.py`, the
`atelier_learning_reconcile` tool, and the `by_project` view + its test (all mirror-only).

## 7. Migration & sequencing

Overriding invariant: **a reader must find a learning by facet / flat-path BEFORE the
folder or tree it used to depend on is removed.** Indexing and dual-read land first;
deletions land last. Phases (detail in the implementation plan):

```
P0 safety net      golden recall/bootstrap/surfacing tests; census the ~100 damaged
                   records by entry_id (target_topic == a lexio layer token)
P1 schema additive aspect[], typed links, target_topic→optional, schema_version 5
P2 DB + reindex    0002_learning_facets.sql; populate facets at reindex (no reader uses
                   them yet — pure additive)
P3 resolver        recall/search filter on learning_facets; assert == P0 goldens
P4 flatten         migrate by-topic → notes/<YYYY-MM>/; repoint 4 enumerators;
                   accept() writes one flat file; project._is_known() → facet query.
                   Trees still present (unread) for git-reversibility.
P5 absorb fix      layer→aspect primary, also_in→aspect secondary, preserve typed links,
                   STOP flattening target_topic; write flat
P6 repair          fix the ~100 damaged records in place (keyed on entry_id); recover
                   also_in from the live workshop note — MUST precede workshop freeze
P7 delete trees    remove by-topic/ + by-project/; delete reconcile.py, D7, by_project view
P8 workshop retire repair → final one-shot drain → freeze → re-point lexio emitter to the
                   new dialect (no recurring absorb cron)
P9 personas        9a rename (behavior-preserving) · 9b delete persona artifacts (last)
```

### Lexio repair (the §2.2 cleanup)

For each damaged `entry_id` from the P0 census: move the flattened value
`target_topic → aspect[]` (primary), restore `also_in → aspect[]` (secondary) by reading
the **live** workshop note at `~/<vault>/workshop/products/lexio/memory/`, and
clear `target_topic` unless the value is genuinely a global topic. In place, idempotent,
keyed on `entry_id` (no id churn). This is why P6 must run **before** workshop is frozen
or deleted — the `also_in` source lives only in the workshop copy.

## 8. Non-goals (explicit — separate RFCs)

- **Generalizing the dream cycle** to consolidate the unified work-episode store
  (learnings + former workshop) into the wiki. This RFC earns the right to it by making
  classification reorganizable; the consolidation pass itself is future work.
- **Wiki-as-RAM**: push-injecting a working set of wiki/profile knowledge at session
  open, with the resolver drilling from RAM (wiki) to disk (raw/, learnings/).
- Mobile capture; hybrid vector search (sqlite-vec / embeddings).

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Dark-learning regression** — a note stops surfacing after the move | P0 golden parity held through P3/P4; `atelier_learning_surfacing_audit` snapshot before P4, `diff` after P7 must show **empty `newly_dark`**. This is the safety instrument the move is gated on. |
| **Damaged-record ambiguity** — can't tell a flattened `layer` from a real global topic | P0 census fixes the damaged `entry_id` set *before* any mutation, using the lexio layer-token allowlist. |
| **`also_in` already lost in the store** | recover from the live workshop note; therefore P6 repair **before** P8 freeze/delete. |
| **Active lexio emitter** keeps writing the old dialect | P8 order: repair → final drain → freeze → re-point. No new damaged records after freeze. |
| **Persona rename half-applied** — a missed `space_by_role` caller throws at runtime | 9a is one lockstep commit; `_VALID_WRITERS` + synthesis + callers + tests change together; tests are the oracle. |
| **Reindex non-determinism / duplicate facet rows** | clear-and-repopulate per page (follow the concept-edge DELETE-then-INSERT shape); test: reindex twice → identical `learning_facets`. |

## 10. Verification (acceptance)

- Golden parity tests (recall / bootstrap / surfacing) pass unchanged P0 → P4.
- Surfacing-audit `diff` after P7 has empty `newly_dark`.
- Flatten + repair scripts are idempotent (second run = no-op, `entry_id`s stable).
- `learning_facets` is deterministic across reindex; many-valued `aspect[]` → N rows.
- Corrected absorb unit test: `layer`→aspect primary, `also_in`→aspect secondary,
  `target_topic` absent, typed links preserved.
- End-to-end: `atelier reindex` clean on the live vault; `atelier doctor` green (D7
  removed); a capture→accept cycle lands one `notes/<YYYY-MM>/<id>.md` with correct facet
  rows and is recalled by its own concept.
