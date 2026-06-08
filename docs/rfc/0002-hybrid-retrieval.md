# RFC 0002 — Hybrid retrieval: semantic + lexical + relational

| | |
|---|---|
| **Status** | Proposed |
| **Scope** | the retrieval layer (`runtime/search/`, `runtime/index/`, recall/search) |
| **Builds on** | RFC 0001 (flat facet store) — facets become *filters* layered on hybrid retrieval |
| **Reference** | `github.com/garrytan/gbrain` — atelier's lineage (`atelier:gbrain`); this RFC brings atelier's retrieval up to gbrain's model |
| **Depends on** | a runtime embedding gateway (new; atelier had deferred this to v0.3) |

---

## 1. Summary & thesis

atelier's **write side is sound** — markdown is truth, the DB is a rebuildable
projection, an auto-link graph and a dream cycle already exist (all mirrored from
gbrain). The **read side is a primitive subset**: a single lexical stage (FTS5
BM25 over markdown *bodies*), plus two hand-tuned boosts. There is **no semantic
retrieval**, so a note is findable only if the query word literally appears in its
body.

That one gap is the root cause of the whole class of problems we keep patching by
hand: **dark learnings**, the concept-probe gymnastics, "aspect is too coarse for
the probe," navigational pages going dark. Every one is a symptom of lexical-only
retrieval — we have been *simulating* semantic search with manual concept edges.

This RFC upgrades retrieval to gbrain's model: **three complementary search modes
— semantic (vector), lexical (BM25), relational (graph edges) — fused with
Reciprocal Rank Fusion, optionally reranked**, behind one resolver. It also closes
the **coverage blind spot** (frontmatter, `.yaml`/structured, optionally code are
indexed, not just markdown bodies).

When this lands, the surfacing audit stops being a firefighting instrument and
becomes a *quality metric* — semantic recall means a learning no longer has to
echo its own concept in its body to be found.

## 2. Current state (what's broken, precisely)

```
INDEX:  walk_markdown(*.md only) → split_frontmatter + chunk_body(paragraph)
        → pages · chunks · chunks_fts(FTS5 BM25) · links · learning_facets
RESOLVE: fts.search()      FTS5 BM25 over bodies                    (general)
         recall.rank_hits  FTS + concept-overlap boost + project boost + facet filter
         search.search     FTS + facet EXISTS filter
         graph.py          BFS over links (used by atelier_links only, NOT recall)
```

Gaps vs gbrain (`hybrid.ts`: intent → expansion → vector+BM25+RRF → graph augment
→ reranker → budget → dedup):

| | gbrain | atelier today |
|---|---|---|
| semantic (vector) | pgvector HNSW, 16 providers | **none** |
| lexical (BM25) | yes | yes (FTS5) |
| relational (graph) | post-fusion graph-signal boosts | graph exists but **not wired into recall** |
| fusion | RRF | n/a (single stage) |
| reranker | cross-encoder (zerank-2 / local) | none |
| coverage | body + frontmatter + tables + code + multimodal | **body only** |
| chunking | recursive / semantic / tree-sitter / contextual | naive paragraph |
| output | `think`: answer + citations + gaps | raw hits injected |

## 3. Target model

One **resolver** (`runtime/search/resolver.py`) orchestrating three modes behind a
pluggable engine contract (gbrain's BrainEngine pattern):

```
query
  │  (optional) intent-classify + expansion          [phase ≥5]
  ▼
 ┌─ semantic ──  vector kNN over embedded chunks (sqlite-vec)
 ├─ lexical  ──  FTS5 BM25 (existing)
 └─ relational ─ graph traversal over links/concept edges
        │
        ▼  Reciprocal Rank Fusion (each mode votes; no global weight)
   fused candidates
        │
        ▼  post-fusion boosts: facet match (RFC 0001), project, recency,
        │                      source-tier, graph-adjacency (floor-ratio gated)
        ▼  (optional) cross-encoder reranker                [phase 5]
        ▼  token-budget + dedup-by-page
     results  → recall injects; atelier_search returns; (think synthesizes) [phase 7]
```

**Facets stay as filters, not rankers** — RFC 0001's `learning_facets` becomes a
`WHERE EXISTS` pre-filter on the fused candidate set (scope to project/aspect/topic),
preserving the project-local vs global separation.

**Invariants preserved:** markdown is truth; the DB (now incl. vectors) is a
rebuildable projection (`rm cache && reindex` still works — embeddings re-generate,
gated by an `embedding_signature` for cost); the dream cycle, schema overlays, and
the single-writer locks are untouched.

## 4. Storage decision — `sqlite-vec`, behind an engine contract

Two options, mirroring gbrain's two-engine design:

| | `sqlite-vec` (recommended) | PGLite / pgvector (gbrain's) |
|---|---|---|
| fits atelier today | ✅ single file, zero-config, `rm db && reindex` intact | ✗ new Postgres dependency/daemon |
| vector search | `vec0` virtual table, brute-force + (ANN roadmap) | HNSW (mature ANN) |
| scale ceiling | ~tens of thousands of chunks (single-user vault) | 100K+ pages, multi-machine |
| migration cost | low (extension load + a vtable) | high (engine swap, ops) |

**Recommendation:** adopt **`sqlite-vec`** to keep atelier's single-file, laptop-first,
rebuildable model — but introduce a **`RetrievalEngine` interface** (gbrain's
contract-first lesson) so `pgvector` is a drop-in future implementation if the vault
ever outgrows SQLite. Decision recorded here; revisit at the scale ceiling.

## 5. Embeddings — a runtime gateway (new capability)

atelier had no runtime LLM/embedding path. This RFC introduces a minimal
**embedding gateway** (`runtime/ai/gateway.py`):

- **Providers:** a local default (Ollama / llama.cpp — the vault is *personal*, so
  on-device embedding avoids shipping private content to a hosted API) + an optional
  hosted provider (OpenAI / Voyage) for quality. Provider + model + dim recorded in
  config (`~/.atelier/config.yaml`), keys in `~/.atelier/secrets/.env`.
- **Stale detection:** an `embedding_signature` (provider+model+dim+chunker_version)
  stamped per chunk; reindex re-embeds only changed/stale chunks (cost control).
- **Privacy:** local-by-default; if a hosted provider is configured, respect the
  same exclusion rules as §6 (no `*.local.*`, no secrets). The cache DB is never
  pushed, so embeddings stay machine-local.

## 6. Coverage — close the blind spot

Extend ingestion beyond markdown bodies so the resolver has no blind side:

- **Frontmatter** — index frontmatter values as searchable text (today they live
  only in the JSON blob / facet table; FTS can't see them — the original
  dark-learnings root). gbrain indexes frontmatter at import.
- **Structured `.yaml` / `.json`** — `crawl` walks `*.md` **+ `*.yaml`/`*.yml`
  (+ `.json`)**, flattening `key: value` into chunk text, classified as a `data`
  page_type. **Exclude** `*.local.*`, `**/secrets/**`, `~/.atelier/secrets/**`
  (privacy). This makes lexio's `contracts/C-*.yaml`, `holds.yaml` discoverable —
  the concrete blind spot that motivated this RFC.
- **Code (optional, later)** — tree-sitter symbol-aware chunking (gbrain's
  `chunkers/code.ts`) if/when code lands in the vault. Deferred.

## 7. Chunking — semantic + contextual

- Replace naive paragraph split with a **recursive, heading-aware** chunker first
  (low effort, CJK-aware per the vault's bilingual content).
- Then **contextual retrieval** (Anthropic's technique gbrain uses): prepend a
  short doc-level context string to each chunk *before embedding*, so a chunk is
  retrievable even when the disambiguating context lives elsewhere in the file.
  This is the single highest-leverage recall improvement after embeddings exist.

## 8. Migration & phasing (execute incrementally next session)

Each phase ends green; retrieval quality is measured before/after (§10).

```
P0  Eval harness + RetrievalEngine contract
      extend the surfacing audit into a proper P@k / R@k harness over the vault
      (gbrain has evals/ + a calibration gate); define RetrievalEngine interface.
      → BASELINE numbers for today's FTS-only retrieval.
P1  Coverage (cheap lexical win, no embeddings)
      index frontmatter + *.yaml/structured into FTS; exclusions per §6.
      → closes the blind spot; re-measure.
P2  Embeddings substrate
      sqlite-vec vec0 table; runtime/ai/gateway.py (local default); embed at
      reindex with embedding_signature stale-detection.
P3  Hybrid fusion
      resolver.py: vector kNN + FTS BM25 → RRF; recall/search route through it;
      retire the ad-hoc concept-overlap/project hand-boosts (now post-fusion).
P4  Relational
      wire graph signals into the resolver (concept-edge adjacency, cross-project,
      session-demote) as floor-ratio-gated post-fusion boosts.
P5  Reranker (optional)
      cross-encoder (local llama-server or hosted) final reshuffle.
P6  Contextual chunking
      recursive/heading-aware + context-prefix-before-embed.
P7  Synthesis (optional, deferred)
      a `think`-style answer+citations+gap layer over the resolver.
```

P1 stands alone (ship it even if embeddings slip). P2–P4 are the core upgrade.
P5–P7 are quality/UX gravy.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Embedding cost / latency on reindex | `embedding_signature` re-embeds only stale chunks; local provider default; batch. |
| `rm db && reindex` becomes expensive (re-embed all) | persist embeddings keyed by `content_hash` in a side cache so a DB rebuild reuses them; only content changes pay. |
| Privacy — hosted embeddings leak vault content | local-by-default; hosted opt-in; §6 exclusions; cache DB never pushed. |
| `sqlite-vec` ANN immaturity at scale | brute-force is fine at single-user scale; RetrievalEngine contract lets pgvector swap in if needed. |
| Quality regression vs today | P0 eval harness gates every phase; the surfacing audit's `newly_dark` stays a hard gate (RFC 0001 discipline). |
| Scope creep (intent classify, expansion, multimodal) | explicitly phased P5+/deferred; P1–P4 are the contract. |

## 10. Verification

- **Eval harness (P0):** P@5 / R@5 over a labelled probe set drawn from the vault
  (seed from the surfacing audit's self-probes). Every phase reports the delta;
  gbrain cites P@5 49% / R@5 98% and +31pt from graph — atelier gets its own
  baseline at P0 and must improve, never regress.
- **Surfacing audit stays the omission gate:** `newly_dark` empty across each phase.
- **Determinism preserved:** `rm cache && reindex` reproduces the same DB +
  embeddings (modulo provider non-determinism, which the signature pins).
- **Blind-spot closed (P1):** `atelier_search` finds a `.yaml` contract by content.
- **Semantic win (P3):** a learning whose concept is NOT in its body is recalled by
  a semantically-related query — the dark-learnings class dissolved, demonstrated
  with a fixture that fails today.

## 11. Non-goals (this RFC)

- Multimodal (image) embedding — gbrain has it; defer until the vault needs it.
- A hosted multi-machine deployment / pgvector migration — contract leaves the door
  open; not built now.
- Replacing the dream cycle or facet model (RFC 0001) — this layers on them.
- `think`-style synthesis is P7/optional, not core.
