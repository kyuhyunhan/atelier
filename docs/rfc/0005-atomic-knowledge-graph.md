# RFC 0005 — Atomic knowledge graph: claims + entities, three layers, single-sourced structure

| | |
|---|---|
| **Status** | Draft (proposed 2026-06-18) |
| **Scope** | the whole content model (`raw/` → `graph/` → projection); the node ontology; `entry_id` derivation; structure single-sourcing; every ingest / derive / recall pipeline |
| **Builds on** | RFC 0001 (facets-not-paths), RFC 0003 (one graph, deterministic ingest, LLM off the write path) |
| **Revises** | RFC 0003 directory naming (`provenance/` → `raw/`); extends the entity layer from per-source pages to **atomic claims + entities** |
| **Substrate** | Labeled Property Graph; node unit modeled on **nanopublications**; relation vocabulary from **PROV-O** + **SKOS** |
| **Schema** | → v7 (three node classes: `source` / `entity` / `claim`, content-addressed) |

---

## 1. Summary & thesis

Every structural defect observed across the operating history of this vault — repeated
`raw/`↔`provenance/` path drift across ~18 code sites, three divergent `entry_id`
derivations, captures landing in a content tree by channel accident, per-source summary
pages that mirror *origin* rather than *knowledge*, and an operational-capture island
disconnected from the reference wiki — is **one root wearing many masks**:

> **The vault's structure is not single-sourced, and its directory paths encode three
> orthogonal axes at once — channel (how a thing entered), domain (what it is about),
> and lifecycle (staged vs processed).**

This produces two failure modes: **drift** (change the structure once, ~20 other
re-declarations lag) and **conflation** (a single path does several jobs, so concepts
duplicate per domain and channel leaks into content location).

**Thesis.** Fix it at two layers, the second being the real cure:

1. **Model** — collapse the content model to an **atomic property graph** of two derived
   node classes, **Entity** (a canonical subject) and **Claim** (one atomic assertion),
   built from immutable **Source** artifacts. Classification lives entirely in
   **frontmatter fields** (kind, domain, surfacing, sensitivity, project), never in the
   path. Directories are cosmetic/sharding only.
2. **Single-sourcing** — define structure (paths, ids, page types) in **one** canonical
   place that every consumer derives from. No hardcoded paths anywhere. This is what makes
   the cure permanent; the `provenance/`→`raw/` rename in this RFC is its first proof
   (it must be a one-line change).

The result is one graph in which personal, knowledge, operational, and project
(workshop) intake all flatten into shared Entity/Claim nodes, content-addressed and
interoperable (PROV-O/SKOS-aligned), retrieved through a single projection.

## 2. Current state (the faults, observed)

- **Hardcoded structure in ~18 runtime files** (path literals for `raw/wiki/provenance/graph/...`)
  *in addition to* the schema overlays — the overlays were meant to be the single source but
  code re-declares paths independently. Every rename drifts.
- **Three `entry_id` conventions, two namespaces**: content-time-based (legacy), content-id
  based (youtube `video_id`), and **path-based** (`new_doc`/`pending`, `uuid5("atelier:"+rel)`).
  The path-based one mutates id on move → breaks links **and** R2 asset keys (`{entry_id}/…`).
- **Channel→content coupling**: `capture` (a reserved *mobile channel* scaffold) hardcodes its
  landing to `personal/inbox/`, so any captured note is decreed "personal" by channel accident.
- **Origin-shaped derived layer**: one summary page per source mirrors the input document, not
  the unit of retrieval/reuse (an idea). Cross-source connection is weak.
- **Two disconnected classification systems**: learnings classify by free-form facets
  (`target_topic`/`aspect`); the wiki classifies by `entities/`. They share no canonical
  subjects, so operational memory and the reference wiki cannot traverse to each other.

## 3. Target model — three layers

```
raw/    (L1)  Source nodes — immutable artifacts + full provenance metadata
              cosmetic intake dirs: personal/ · knowledge/<subdomain>/ · inbox/ · workshop/<project>/
graph/  (L2)  Entity + Claim nodes — one flat space (date/id-prefix shards at scale; NEVER by kind)
projection (L3)  SQLite + vector at ~/.atelier/cache — derived, rebuildable
```

**Invariant rule.** Directories are cosmetic and for shard-scaling only. **All classification
is frontmatter fields. The projection (L3) reads fields, never the path.** Four intakes
(personal · knowledge · inbox · workshop) converge into a single Entity/Claim graph.

### 3.1 Substrate & unit

- **Labeled Property Graph**: markdown files are nodes, frontmatter is properties, links are
  typed edges. Not an RDF triple store (join cost), but it **borrows RDF vocabularies**
  (PROV-O, SKOS) as field/relation names so semantics and future RDF/nanopub export are
  near-mechanical.
- **Node unit modeled on nanopublications**: every Claim separates *assertion* + *provenance*
  + *publication-info*.

### 3.2 Intake — no staging directory

A raw Source lands **directly in its domain dir** (`raw/knowledge/`, `raw/personal/`, …) — there
is **no `_new/` staging dir**. "Awaiting atomization" is not a place but a derived state: a Source
with no Claim `derived_from` it. Subdomain is a *field* set at atomize, not a directory the doc
must be moved into. (Consequence: the Web Clipper is repointed from `_new/` to `raw/knowledge/`;
this **supersedes** the prior "`_new/` staging must be preserved" convention, which existed only
for the old move-on-ingest flow.)

## 4. The schema (v7)

Common base (every node): `entry_id`, `schema_version`, `kind`, `created_at`,
`content_hash`, `sensitivity`, `links[]`. Kind-specific fields extend it (base + typed
extension — the same discipline as `base.yaml` + overlay).

### 4.1 `source` (L1) — `prov:Entity`, the origin anchor. CORE + per-source-type extension
```yaml
# CORE (every raw doc)
entry_id; schema_version; kind: source; created_at; content_hash
title; sensitivity; domain(personal|knowledge|inbox|workshop)   # legacy 'provenance' field → domain
attributed_to                       # PROV-O wasAttributedTo: authoring channel
# COMMON-OPTIONAL (preserved when present)
summary?; collected_at[]; edited_at[]; word_count
embedded_assets[]                   # R2 asset keys — MUST be preserved
# EXTENSION by source_type
youtube:      source_url; source_type; channel; channel_url; duration_sec; language; transcript_source
web_clipper:  source_url; source_type
personal:     (none)
# body = original artifact, immutable
```

### 4.2 `entity` (L2) — `skos:Concept`-aligned subject
```yaml
entry_id; schema_version; kind: entity; created_at; content_hash; sensitivity
pref_label; alt_label[]             # skos:prefLabel / skos:altLabel
type(Person|Concept|Work|Place|Tool|Event|Domain|Organization|Project|Emotion|Role)
in_scheme[domain]                   # skos:inScheme
gloss?
links: [ {to, rel, why} ]           # rel ∈ broader|narrower|related (SKOS)
```

### 4.3 `claim` (L2) — nanopublication *assertion*
```yaml
entry_id; schema_version; kind: claim; created_at; content_hash
# assertion
statement                           # one atomic assertion
is_about: [→entity id]
links: [ {to, rel, why} ]           # rel ∈ supports|refutes|refines
context?                            # context preservation (anti-loss)
# provenance (PROV-O)
derived_from: [→source id]          # wasDerivedFrom
attributed_to                       # wasAttributedTo (author/speaker of the claim)
generated_by                        # wasGeneratedBy (ingest|atomize|promote|dream)
# recall policy (atelier-specific)
surfacing(query|proactive|always); domain; project?; sensitivity
# EXTENSION (inbox/learning-derived, optional)
observation_kind?; ac_status?; agent_kind?; hook?; session_id?; working_dir?; why_status?
```

## 5. `entry_id` — content-addressed, single convention

One derivation, defined once, namespace reused from the existing vault NS:
```
source entry_id = uuid5(NS, created_at | discriminator)   # discriminator: video_id|url|hash
entity entry_id = uuid5(NS, type | pref_label)            # same subject → same id = dedup
claim  entry_id = uuid5(NS, normalize(statement) | derived_from)
```
- **Path-based derivation is retired.** Ids are content/identity-based, hence stable across
  rename / move / shard — required because **links and R2 asset keys depend on id stability**.
- `links`/`is_about`/`derived_from` reference `entry_id`, never a path → rename-safe.
- Entity id-as-content-hash *is* the canonicalization/dedup mechanism that connects sources.

## 6. Surfacing (recall tiers)

`surfacing` is a static eligibility ladder: `query ⊂ proactive ⊂ always`. Push at recall is
**context-scoped**:
```
recall = gate(surfacing) × domain_prior(context) × vector_relevance × sensitivity_gate
  on-query (T2): universal — any node, prior ignored
  proactive (T1): per-turn, ranked by context; coding session → operational/current-project high, knowledge mid, personal low
  always (T0): unconditional within domain scope; small, capped; dreaming distills into it
```
`private` nodes are never pushed (reachable only by explicit query). T0 has a hard budget cap.

## 7. Extraction pipeline (one source → claims + entities)

```
Source → atomize →
  1 claim extraction      (body → atomic assertions)
  2 entity recognition    (subjects per claim)
  3 entity linking/dedup  (resolve to existing entity by content-id, else create)
  4 relation extraction   (entity↔entity broader/related; claim↔claim supports/refines)
  5 emit                  (Claim: derived_from→source, is_about→entities; new/updated Entity; edges)
```
Idempotent: `content_hash` dedups claims, content-id dedups entities. Claims are minted
per-source; entities are resolve-or-create (shared, canonical) — this is what links the graph
across sources and domains.

### 7.1 The learning lifecycle is surfacing tiers, not directories

`candidate` / `note` / `principle` are not distinct content types — they are one Claim at
different surfacing tiers plus an acceptance state. The same directory→field collapse applied
to `sources/` and `entities/`:

| legacy | new (Claim fields) |
|---|---|
| `learnings/candidates/` | `surfacing: query`, `ac_status: pending` |
| `learnings/notes/` (accepted) | `surfacing: proactive`, `ac_status: passed` |
| `learnings/principles/` | `surfacing: always` (T0) |

- An operational learning is domain-known at capture (the Stop/SessionEnd hook knows "this is a
  learning"), so it is born **directly as a Claim** (`domain: operational`,
  `surfacing: query`, `ac_status: pending`, `generated_by: <hook>`) — not via `inbox`
  (which is for domain-*undetermined* manual captures). Its Source is thin session metadata
  (`session_id`, `working_dir`, `attributed_to: claude-code`).
- **promote** = elevate `query → proactive` behind the acceptance gate (a field transition).
- **dream** = distill `proactive → always` into the capped T0 budget **and** synthesize new
  Claims (cross-claim generalizations linked by `refines`/`supports`, `derived_from` the source
  claims). Its value is now exact: it is the T0-budget curator (§6), not a directory mover.

### 7.2 Ongoing operation — triggers

Steady-state principle: **automate the deterministic, gate the generative, and nudge so nothing
silently backs up.**

| edge | trigger | rationale |
|---|---|---|
| capture (operational learning) | **hook** (SessionEnd/Stop), automatic | cheap, additive; acceptance-gated downstream |
| intake (web-clipper / youtube) | **event** — raw Source lands directly in `raw/knowledge/` (no staging; "un-atomized" = a Source with no derived Claim) | — |
| reindex / projection (L2→L3) | **automatic** — the autosync poller (already running, 30 s, quiescence-gated) reindexes changed files alongside the git commit | deterministic, idempotent (`content_hash`), no judgment; **removes the drift class structurally** (manual reindex was the root of the D2 / space-label drift) |
| atomize (L1→L2) | **nudge-driven** — a cadence surfaces "N un-atomized sources" (like the dream nudge); the human runs `atelier-atomize` | LLM judgment + cost + privacy (personal) ⇒ no blind cron; the nudge prevents backlog without surrendering the quality/privacy gate |
| dream (proactive→always) | **cadence nudge**, manual | what earns always-inject is high judgment → human-gated |
| autosync (git) | 30 s poller | unchanged |

## 8. Single-sourced structure (the permanent cure)

One canonical structure definition (`schema/data/structure.*`) + a runtime resolver for
paths and id derivation. **Every** writer, classifier, reindexer, hook, and skill derives from
it; zero hardcoded path literals. The `provenance/`→`raw/` rename then becomes a single-constant
change — and that one-line-ness is the acceptance test for this layer being done.

## 9. Migration & sequencing (gated; lossless extraction before atomization)

**Two inputs, full coverage.** The migration has two distinct inputs and covers *every*
existing document:
1. **Existing `graph/` derived pages** (277 sources + 603 entities) → re-expressed as atomic
   Claims/Entities (P3 lossless extraction → P4 atomization; link-set diff = ∅).
2. **All `raw/` documents** (600 personal + 86 knowledge + 191 learning) → renamed and **always
   preserved as immutable Source nodes (P2)**, then **fully atomized** into Claims/Entities (P4,
   batched). Learnings follow §7.1.

**Atomization is additive.** A raw Source is never destroyed; Claims sit *on top* of it. So
even an imperfectly atomized narrative loses nothing — the original remains, indexed and
on-query. This is what makes the full backfill both *complete* and *lossless*. Atomization is
batched (knowledge-first by value, but **all** raw is covered); narrative-personal content is
backstopped by its raw Source and gated `sensitivity: private` (never pushed).


```
P0  This RFC — ratify schema + plan.                                  [gate: approval]
P1  Single-source structure + resolver; migrate ~18 hardcoded sites + 3 entry_id
    derivations to derive. No behavior change.                        [gate: tests green; 0 hardcoded paths]
P2  Rename provenance/→raw/ via the one constant (proof of P1).       [gate: reindex clean; R2 keys unchanged]
P3  Lossless extraction (deterministic): parse every graph/ page →
    frontmatter + body + all in/out links → structured intermediate.  [gate: edge & attribute coverage 100%]
P4  Atomization (LLM, additive, batched): re-express existing graph/
    pages (bounded by P3 link-map) AND fully atomize all raw/ docs
    (personal+knowledge), knowledge-first by value but covering ALL;
    raw Sources preserved immutable; source ids kept, claim ids minted.
    Learnings/{candidates→query·pending, notes→proactive·passed,
    principles→always} → Claims (§7.1; ids preserved, facets→is_about). [gate: link-set diff vs P3 = ∅; every raw has Source + ≥1 claim or logged skip]
P5  Surfacing + recall: surfacing field; domain/project prior; T0 cap;
    sensitivity gate; capture → raw/inbox; recall/capture-disposition
    hooks updated. Rework atelier-consolidate: promote = query→proactive
    (ac gate), dream = proactive→always distill + synthesis — operating
    on Claim fields, not directories. Wire ongoing triggers (§7.2):
    autosync poller also reindexes changed files; atomize-nudge cadence;
    Web Clipper repointed to raw/knowledge/ (no _new/).               [gate: recall + promote/dream per §6/§7.1; reindex auto-fires on change]
P6  Projection: reindex --full; embed at claim granularity; doctor.   [gate: doctor green; retrieval parity↑]
P7  Retire legacy: old source/entity pages, learnings/* dirs, path-based
    derivation; rewrite TAS skills (atelier-atomize → new Source→atomize→
    Entity/Claim flow; atelier-consolidate → tier-transition model).   [gate: 0 dangling refs; skills route]
```
Each phase: own commit(s), green boundary, no next phase before its gate passes.

## 10. Risks & mitigations

- **Atomization over-fragments / loses context** → P3 lossless extraction is the bounded
  checklist; `context` field + `derived_from` preserve grounding; spot-check gate in P4.
- **Node-count growth (full atomization)** → content-id + `content_hash` keep ingest
  incremental & idempotent; date/id-prefix shards bound directory size; two-stage retrieval.
- **R2 asset breakage on rename** → ids are content-based and stable; P2 gate verifies keys
  unchanged.
- **Big-bang risk on a live, most-developed subsystem** → strict phase gates; P1 (single-source)
  first so later structural changes are cheap and reversible.

## 11. Non-goals

- No RDF triple store (LPG + borrowed vocab only).
- No standing LLM-as-judge on the write path (LLM at atomize + query-time only; consistent with RFC 0003).
- Knowledge *subdomains* stay path-organized as a cosmetic single axis (taxonomy ≠ lifecycle).

## 12. Verification (acceptance)

1. Zero hardcoded structural path literals in runtime (grep clean).
2. One `entry_id` derivation; all content-based; path-based derivation removed.
3. `provenance/`→`raw/` is a one-constant change.
4. P3→P4 link-set diff = ∅ (no relationship/attribute lost).
5. `doctor` all-green post-migration; retrieval parity or improvement vs pre-migration probes.
6. A capture lands in `raw/inbox/` with `source` field, never a content tree.
7. **Full coverage**: every pre-migration `raw/` doc (877) has a preserved immutable Source
   and ≥1 derived Claim (or an explicit logged skip); raw Source count is conserved end-to-end.
