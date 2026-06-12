# RFC 0003 — Provenance consolidation: one graph, faceted provenance, no curation judge

| | |
|---|---|
| **Status** | Proposed (ratified 2026-06-12) |
| **Scope** | the content model across `raw/`, `wiki/`, `learnings/`; the link graph; directory normalization; the role of the LLM |
| **Builds on** | RFC 0001 (facets-not-paths) extended vault-wide; RFC 0002 (the page-type-agnostic resolver) |
| **Reference** | `github.com/garrytan/gbrain` — deterministic ingest, LLM only at query time |
| **Unblocks** | RFC 0002 P4 (relational mode) — dead today because learnings are graph-isolated |
| **Schema** | v5 → v6 (additive: `provenance` + consistent `sensitivity` facets; `touches` as typed link) |

---

## 1. Summary & thesis

atelier holds three kinds of provenance — **personal** (first-person life record),
**knowledge** (compiled external references), **learning** (behavioral lessons from
dev work) — plus a derived **entity layer** (`wiki/` today: named nodes + links).
RFC 0002 gave them a strong, *provenance-agnostic* retrieval substrate (lexical +
semantic + relational, fused). But two structural faults remain, and they are the
same fault wearing two masks:

1. **Classification lives in the filesystem, not in facets.** `raw/personal/` vs
   `raw/knowledge/` encodes provenance in the *path*; `sensitivity` is set only on
   raw pages, never on wiki or learnings. This is exactly the anti-pattern RFC 0001
   killed for `learnings/` — unfinished vault-wide.
2. **Learnings are an orphan island in the graph.** They participate in retrieval
   lexically and semantically, but the relational mode (RFC 0002 P4) is dead for
   them because they hold almost no resolved graph edges.

**Thesis.** The fix is *not* to merge the trees into one wiki, and *not* to build a
standing LLM-as-judge curation tier. It is to (a) make **provenance and sensitivity
first-class facets**, (b) connect every provenance into **one entity graph built
deterministically from typed links** (gbrain's model — zero LLM at ingest), and (c)
confine the LLM to exactly **two roles, both off the write path**: query-time
synthesis and a gated, one-shot, reviewable backfill. The entity layer stays — but as
the *shared backbone every provenance links into*, not a third content silo.

## 2. Current state (the data, measured 2026-06-11)

Read-only spike over the live vault:

```
provenance     pages   sensitivity_set   graph-connected (resolved edge in|out)
personal        600       600 / 600        508 / 600   (84%)   ← fine
knowledge        83        83 /  83          81 /  83   (97%)   ← fine
wiki (derived)  609         0 / 609         608 / 609   (99%)   ← dense backbone
learning        162         0 / 162           2 / 162   ( 1%)   ← ORPHAN ISLAND
```

```
touches values (distinct): 201   →  exact-match an existing entity slug:  2  (0%)
topic   values (distinct):  37   →  exact-match an existing entity slug:  1  (3%)
concept-edge rows:         335   →  resolved (to_page_id set):            2
```

Three readings:

- **Learnings are 1% graph-connected** while everything else is 84–99%. The dead P4
  is fully explained: the one provenance the resolver serves is the one disconnected.
- **`touches` resolves to almost no entity (0%)** — because those concept-entities
  *do not exist yet*. The gap is missing **nodes**, not missing links. This makes the
  fix *mostly deterministic*: materialize the node, then the edge is a parse.
- **`sensitivity` is half-applied** (raw only). Privacy rules and provenance scoping
  cannot rely on a field that 771 of 1472 pages leave null.

## 3. Target model — three layers (there is no curation layer)

```
L2  Applications        coding-recall · knowledge-Q&A · essay-assist · query-time
        ▲           │   synthesis · dream cycle   (reads; writes propose diffs)
        │ retrieve  ▼
L1  Substrate           pages · chunks · {lexical, semantic, relational/links} · facets
        ▲               the GRAPH is just another deterministic projection here:
        │ index         typed-link extraction + entity-stub creation are a PARSE,
        │               not a judgment (provenance-AGNOSTIC — RFC 0002; extended).
L0  Provenance store    personal · knowledge · learning   (markdown = truth)
        (keyed by provenance + sensitivity fields; product is a separate space)
```

**There is deliberately no "curation layer."** What that name used to cover
decomposes into layers that already exist: deterministic edge-extraction +
entity-stub creation are **indexing** (L1 — same status as building `chunks_fts` or
the vectors); synthesis is a **query-time read** (L2); the legacy-corpus backfill is a
**one-shot migration** (§5.2), not a standing tier; dedup / principle-synthesis is the
existing **dream cycle**, a gated background app that *proposes* reviewable diffs off
the write path. The entity nodes are not a curation tier's output — they are ordinary
content nodes that serve as the graph backbone, authored with link discipline like any
other page.

**Invariants preserved:** markdown is truth; the DB (incl. vectors + graph) is a
rebuildable projection; single-writer-per-subtree; the resolver stays
provenance-agnostic (it fuses by `page_id`; provenance is a *query scope*, never an
engine concept).

### 3.1 Provenance + sensitivity as first-class fields, not paths

Both are **single-valued per page**, so — exactly like `sensitivity` already is in the
schema today (`pages.sensitivity`, a generated column from frontmatter) — they live as
**generated columns on `pages`**, not as many-valued facet rows (`learning_facets`
stays for the genuinely many-valued tags, e.g. `touches`):

- `provenance ∈ { personal, knowledge, learning }` — *where it came from*. (Product
  work is *output*, not memory-provenance — its lessons are captured as `learning` —
  so `product` is a separate space, not a provenance value.)
- `sensitivity ∈ { private, public }` — *who may see it* — set on **every** page (the
  gap today: only raw sets it), driving the hosted-embedding / sync exclusions
  (RFC 0002 §5–6) and the personal "never distilled" rule.

These are **orthogonal axes** (some knowledge is private; some personal writing is
shareable), so they are two fields, not one folder split. Retrieval scopes by them
(`Scope` gains optional `provenance` / `sensitivity` filters); the engine never
branches on them.

### 3.2 One entity graph, built deterministically

The **entity graph** is the shared backbone. Personal entries, knowledge refs, and
learnings all link *into* the same canonical entities (domain / concept / person /
project / place — entity **subtypes**; the 19 existing `themes` become the `domain`
subtype, the coarsest hub), so "what have I learned *and* lived around project X" is
one graph query — while a personal node stays `private` and is never distilled into a
shareable node. **The graph connects; the fields gate.**

Edges are produced by **deterministic parsing of typed links** (gbrain's model,
already half-built in `linker.py`):

- A page references an entity with an explicit link: `[[entities/dependency-direction]]`.
- `put`/reindex extracts the edge by pattern-match — **zero LLM at ingest**.
- A learning's `touches`/`topic` becomes a **typed link to a canonical entity**, not
  a free-text string — so it resolves *by parsing*, not by inference.

## 4. Directory normalization

Principle (RFC 0001, vault-wide): **folders encode only the intrinsic and stable —
provenance + coarse genre + time. Everything else — topic, aspect, sensitivity — is
a facet, never a path.**

```
provenance/                         ← L0 truth, "what came in" (memory inputs)
  personal/  {diary,essay,faith,worklog}/<YYYY>/…    genre + time intrinsic
  knowledge/ <domain>/…
  learning/  candidates/ · notes/ · accepted/ · archived/ · principles/   stage, not topic
graph/                              ← entity backbone (markdown truth, authored)
  entities/  (domain|concept|person|project|place subtypes)
product/                            ← work output, NOT provenance (separate space)
  <name>/…
```

- **`raw/` → `provenance/`**, **`wiki/` → `graph/`** (P1/GP1), **`learnings/` →
  `provenance/learning/`** (P6) — the renames make the *role* (source vs entity
  backbone) legible, which `raw`/`wiki` obscure, and place learnings under the
  provenance they declare (`provenance: learning`) instead of a misleading sibling
  tree. The §8 plan originally scheduled only the first two; P6 (separate doc:
  `0003-p6-learning-relocation.md`) finishes the directory vision this section drew.
- **`themes/` are kept, reclassified as the `domain` entity subtype** (measured: they
  are richly-connected domain hubs, in-deg mean 67 / out-deg mean 56 — the coarsest
  backbone nodes, not synthesis). **`digests/` and `synthesis/` are retired to
  query-time** (§6, P5) — those *are* pre-baked synthesis. So 19 themes fold up into
  entities; 43 synthesis artifacts are archived, not stored.
- `sensitivity` / `provenance` → **fields** (§3.1); `topic` / `aspect` / `touches` →
  **facets** — so a reclassification is a frontmatter edit, never a tree migration.
- Dates and coarse genre stay folders (stable, aid human navigation + markdown-truth).

## 5. The entity graph — deterministic build + one-shot backfill

### 5.1 Going forward (deterministic, zero LLM)

The agent that *writes* content emits typed entity links at compose time; extraction
is a parse. For a captured learning, `touches: [dependency-direction]` is recorded as
`[[entities/dependency-direction]]`; reindex ensures the entity stub exists
(deterministic slugify, create-if-missing) and writes the edge. Intelligence lives in
the *authoring*; storage stays deterministic.

### 5.2 The legacy corpus (one-shot, gated, LLM at the margins)

The 160 orphaned learnings + 92 orphaned personal pages (§2) were written *without*
link discipline. Connecting them is a **one-shot enrichment**, not an ingest dependency:

- **~90% deterministic:** each distinct `touches`/`topic` value → an entity slug →
  create the stub if missing → write the edge. The 0%-match number means most
  entities are *created*, deterministically, from the value.
- **LLM only for alias-merge:** deciding two surface forms are the *same* entity
  (`anthropic-api` ≡ `claude-api`?), or that a `touches` string maps to an existing
  entity rather than a new one. This is the *only* judgment, it is **gated and
  reviewable** (proposes a merge diff; the user approves), and it never blocks ingest.

## 6. The two — and only two — LLM roles

| Role | Layer | Produces | Why it is determinism-safe |
|---|---|---|---|
| **Query-time synthesis** (`think`, RFC 0002 P7) | L2 read | an *answer* (cited prose + gaps) | ephemeral; mutates no state; non-determinism is acceptable in an answer |
| **Gated backfill / alias-merge** | one-shot migration | a *reviewable diff* of links/entities | off the write path; output is markdown the user approves and can hand-edit |

There is **no standing LLM-as-judge curation tier.** An LLM that *produces state on
the ingest path* is the determinism hazard we reject; an LLM that *produces an answer*
or *proposes a diff* is not. Keep the LLM out of the write path: let it read, and let
it propose changes you approve.

## 7. How retrieval stays in sync

- The resolver (RFC 0002) is unchanged and provenance-agnostic. `Scope` gains optional
  `provenance` / `sensitivity` filters so an **application picks its scope**: a coding
  agent scopes recall to `learning` (always-injected) + `knowledge` (on demand); an
  essay agent scopes to `personal` + `knowledge`. Provenance is a *soft query scope*,
  never a hard silo.
- **Relational (P4) lights up for free** once learnings hold resolved entity edges:
  two learnings sharing a concept become 2-hop siblings *through the entity node*, and
  the existing graph BFS (`graph.py`) surfaces them. P4 becomes the thin adapter its
  contract always intended.
- The P3 concept-overlap hand-boost (`recall._boost`, marked "P4: subsumed by
  relational") is **retired** once the relational signal is real — measured, not
  assumed, against the surfacing gate.

## 8. Migration & sequencing

```
P0  Schema v6: provenance + sensitivity as generated columns (additive); backfill
    sensitivity on entity + learning pages from provenance defaults. Gate re-frozen.
P1  Directory normalization (raw→provenance, wiki→graph) — a path rename + reindex;
    markdown-is-truth makes it a move, not a rewrite. Stop encoding provenance in paths.
P2  Typed-link discipline for NEW content: capture/curate flows emit [[entities/…]];
    touches recorded as a typed link. Deterministic extraction (extend linker.py).
P3  One-shot backfill: deterministic entity-stub creation from touches/topic + edges;
    gated LLM alias-merge pass (reviewable diff). Re-measure learning graph-connectivity.
P4  Wire RFC 0002 P4 relational mode (now it has edges to traverse); retire the
    concept-overlap hand-boost; re-measure concept_grouped + hold the omission gate.
P5  (Optional) Query-time synthesis (RFC 0002 P7) over the connected graph.
```

P0–P1 are pure substrate hygiene (shippable alone). P2–P4 are the consolidation core.
P5 is the deferred synthesis layer.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Entity-space explosion (201 new concept-entities) | stubs are cheap (a slug + backlinks); the gated alias-merge collapses duplicates; review the diff before write. |
| Backfill mis-merges distinct entities | LLM proposes, user approves; merges are reversible markdown edits; never auto-applied. |
| Directory rename breaks references | markdown-is-truth + reindex; a path-map migration; the resolver is path-agnostic (keys on `page_id`). |
| Personal content distilled into shareable nodes by mistake | `sensitivity=private` is a hard gate on distillation and hosted embedding (RFC 0002 §5–6). |
| Regression vs today's recall | the RFC 0002 surfacing `newly_dark` gate + P@k/R@k harness gate every phase, as in P3. |
| Provenance field drifts from the (renamed) path | the `provenance` frontmatter field is the source of truth; the folder is navigational only — read the field, never the path. |

## 10. Non-goals (this RFC)

- Semantic *merger* of learnings into entity nodes (loses the learnings lifecycle —
  this RFC links, never merges).
- A standing LLM curation tier (explicitly rejected — §6).
- Multimodal/personal-image entities (defer).
- Mobile capture of provenance (reserved, per the engine's mobile reservation).

## 11. Verification (acceptance)

- **Connectivity:** learning graph-connectivity rises from 1% toward the 84–99% of
  other provenance; re-run the §2 spike and report the delta.
- **Determinism:** `rm cache && reindex` reproduces the same graph from the typed
  links in markdown — zero LLM on the ingest path (the backfill is a separate,
  one-shot, committed artifact, not part of reindex).
- **Relational win:** a learning sharing a concept with a strong hit surfaces via the
  relational vote where it did not before (RFC 0002 P4 fixture).
- **Gate held:** the surfacing `newly_dark` set stays empty across every phase.
- **Scope works:** a coding-agent scope returns `learning`+`knowledge` and excludes
  `personal`; an essay scope returns `personal`+`knowledge` — provenance as a soft
  filter, demonstrated end to end.
