# RFC 0007 — Single intake front door: born-as-Source + deterministic mint

| | |
|---|---|
| **Status** | Draft (proposed 2026-07-20; revised post-review) |
| **Scope** | the operational-learning write path (`capture` + `absorb`); the `raw/` intake invariant; the `source` domain enum; a **content-addressed** `raw/operational/` Source; freezing (not deleting) the shared `operational-capture` anchor; physical removal of the vestigial `_new/` dir |
| **Builds on** | RFC 0005 (atomic graph, three layers, single-sourced structure), RFC 0006 (memory north-star — one graph, no silent bypass) |
| **Revises** | RFC 0005 §7.1 — an operational learning is no longer *"born directly as a Claim"* on a single shared anchor; it is **born as its own content-addressed Source** in `raw/operational/`, then a **deterministic 1:1 mint** produces the Claim. RFC 0005 §7.2 capture-trigger row restated. Freezes P10's anchor to legacy — see §6. A forward pointer is added to 0005 §7.1/§7.2. |
| **Schema** | adds `operational` to the `source.domain` (and `entity.in_scheme`) enum; adds a `mint` activity to `generated_by`. **No `atomic` marker** (dispatch is structural — §4). No new node class. |

---

## 1. Summary & thesis

The user's stated design intent: **every piece of incoming data lands first in
`raw/` as a Source; Claims are always *derived* from a Source.** The
implementation diverges for one class of input. RFC 0005 §7.1 decided that an
operational learning (a `SessionEnd`/`Stop` capture, or an absorbed Claude Code
memory) is *domain-known at capture*, and therefore **"born directly as a
Claim"** — bypassing `raw/` and hanging off a single shared Source,
`raw/inbox/operational-capture.md` (P10).

That decision was **half-right**:

- **Right** — operational input *is* domain-known, so it must **not** pass
  through `inbox`, which is the domain-*undetermined* triage lane. §7.1's inbox
  reasoning stands and is preserved here.
- **Wrong** — "domain-known" was conflated with "skip the `raw/` Source layer
  entirely." Two independent conclusions were fused. The consequences: (1) **two
  entry tracks** — operational bypasses `raw/`, everything else lands as a
  Source; (2) **flattened provenance** — every operational Claim `derived_from`
  the one synthetic anchor, so you cannot walk the graph from a Claim to the
  session or `~/.claude` memory it came from.

> **Thesis.** Unify the front door **without re-introducing cost and without
> breaking idempotency.** Every input — capture and absorb included — lands as
> **its own content-addressed Source** in `raw/`. The `raw → Claim` edge then has
> **two implementations**, chosen by the domain lane, not by a per-file flag:
> - **deterministic mint** — LLM-free 1:1, for the `operational` lane, where input
>   arrives *already atomic* (the Source body *is* the assertion).
> - **generative atomize** — the existing LLM extraction, for raw material
>   (YouTube → `knowledge`, diary → `personal`) where many Claims are latent.

The anchor is **frozen to legacy** (§6); each *new* Claim `derived_from` its own
Source.

**The idempotency constraint that shapes this design.** `capture` has **no dedup
ledger** — it relies entirely on the claim `entry_id` being content-addressed.
Per `structure.yaml:157`, `claim_id = uuid5("atelier:claim:{statement}|{derived_from}")`.
Today `derived_from` is a **constant** (the fixed anchor), so the same lesson
re-captured across sessions collapses to one claim. A naive "per-session Source"
would make `derived_from` vary and shatter that dedup — the same lesson would
proliferate one-per-session. **Therefore the new `raw/operational/` Source MUST
be content-addressed** (discriminator = `sha256(normalized body/statement)`,
mirroring the existing `learnings:claude:{body_sha}` precedent at
`structure.yaml:140`). Same lesson → same Source id → same `derived_from` → same
claim id. Idempotency is preserved *because* the Source is content-keyed, and the
anchor's only load-bearing job (a stable constant `derived_from`) is inherited by
the content hash.

**What one front door does and does not fix (scoped honestly).** It eliminates
the **enumeration/intake bypass**: a stage that scans "every Source" or "every
Claim" can no longer miss a domain, because there is no second intake path for a
domain to hide in. Across the session that produced this RFC, one of the three
recurring domain-blind bugs — the atomize nudge counting only `kind: source` —
was exactly this enumeration class, and it dies structurally. It does **not**
fix **filtering-asymmetry** bugs: the promote gate special-casing
`ac_status ∈ ("", "passed")` and the dream cadence counting only accepted
operational learnings are *field*-asymmetries that survive this RFC untouched (a
stage can still branch on `ac_status` / `generated_by` / `domain` and forget a
domain). This RFC removes one bug class and narrows another; it does not claim to
abolish domain-blindness by fiat.

## 2. Current state (the fault, observed)

- **Born-as-claim.** `capture.py` and `absorb_claude.py` write Claims directly
  (`generated_by: ingest`, `capture.py:181` / `absorb_claude.py:270`), every one
  `derived_from` `ensure_operational_source()` → the single anchor at
  `raw/inbox/operational-capture.md`.
- **The anchor squats in `inbox`.** `raw/inbox/` is the domain-*undetermined*
  triage lane (RFC 0005 §3). The operational anchor sharing that directory
  conflates "unclassified triage" with "operational-claim anchor storage."
- **No `operational` source lane exists.** The `source.domain` enum is
  `personal | knowledge | inbox | workshop` (`graph.overlay.yaml`). There is no
  `raw/operational/` — which is *why* operational input was forced to
  born-as-claim: it had nowhere to land as a Source. (Note: claim `domain` is
  deliberately enum-free, which is why `domain: operational` already works on
  claims.)
- **Provenance is not traversable.** N operational Claims, one `derived_from`
  target. Session/memory origin lives only on §4.3 extension fields.
- **Vestigial `_new/`.** `raw/knowledge/_new/` holds a lone `.gitkeep`; RFC 0005
  §3.2 already retired the `_new/` staging concept. Un-cleaned artifact.

## 3. Target model — `raw/` is the one front door

Two **orthogonal** "not-yet" states, never conflated:

| axis | what is unresolved | domain known? | resolution | needs a place? |
|---|---|---|---|---|
| **atomization** | Source → Claim not done yet | ✅ yes | a **derived state** (a Source with no Claim `derived_from` it) | ❌ no — `_new/` retired |
| **classification** | the domain itself is unknown | ❌ no | **`raw/inbox/`** — a neutral holding lane until a classify step routes it | ✅ yes |

`inbox` does **not** replace `_new`; they are orthogonal (`_new` = atomization
axis, already replaced by the derived-state model; `inbox` = classification axis,
retained).

**Intake routing** (unchanged for existing sources; new only for operational):

```
input arrives
├─ domain self-evident → raw/<domain>/ as its own Source
│    ├─ YouTube            → raw/knowledge/<sub>/  ─┐  raw material
│    ├─ diary              → raw/personal/         ─┤  → generative atomize (LLM)
│    │                                             ─┘
│    └─ capture · absorb   → raw/operational/       ── already atomic, content-addressed
│                                                      → deterministic mint (no LLM)
│
└─ domain undetermined → raw/inbox/  (domain: inbox/undetermined)
      │  classify step (human / LLM) assigns a domain
      ▼
     raw/<assigned-domain>/  → then atomized like any other Source
```

**Schema deltas (all additive):**
- `source.domain` gains `operational`; **`entity.in_scheme` gains `operational`
  too** (so entities resolved from operational captures are schemed
  consistently — resolves the N3 three-way divergence).
- `generated_by` gains `mint` (the deterministic L1→L2 activity), distinct from
  `atomize` (generative) and `ingest` (L0→L1). Both mint and atomize are L1→L2.
- a dedicated **content-only** Source id template `operational:
  "atelier:operational:{content_hash}"` (no `{created_at}` — see §4), alongside
  the existing `youtube:` content template.
- **No `atomic` source marker.** Dispatch is structural: `domain == operational
  ⇒ mint`, everything else ⇒ atomize (§4).

## 4. The deterministic mint (mechanism)

**Dispatch is the lane, not a flag.** A per-file `atomic: true` boolean was
rejected in review: it is forgettable, and a *missed* flag on a capture is not
fail-safe — because atomize is nudge-driven/manual (RFC 0005 §7.2), a capture
that fell through to atomize would **not be born at capture time at all**,
breaking `capture`'s non-blocking born-immediately contract, and would later be
LLM-rephrased into possibly-multiple claims with new ids. The `operational` lane
is the only already-atomic intake, so the rule is structural and unforgettable:
**`source.domain == operational ⇒ deterministic mint`.**

For an operational Source the engine derives **exactly one** Claim with **no LLM
call**:
- `statement` = the Source's whitespace-normalized body/title, capped as today.
- `derived_from` = that Source's `entry_id` (content-addressed — see below).
- `is_about` = resolved from the Source's declared subjects, as capture does now.
- **acceptance-criteria fields mirrored onto the Claim** — `session_id` /
  `working_dir` / `project_hint` are written to the minted Claim's frontmatter *in
  addition to* the Source's provenance. This is mandatory: `criteria.py` reads
  these off the **Claim** (`_check_tied_to_event` → `session_id`/`working_dir`,
  `criteria.py:132-133`; specificity + `has_project_tag`, `criteria.py:115-117,136`)
  to score the promotion acceptance gate. The old born-as-claim design kept them
  on the Claim for exactly this reason (`capture.py:166-168`); the mint must not
  regress it. (Provenance *also* lives on the Source, giving the graph traversal
  §1 wants — the fields are mirrored, not moved.)
- `generated_by: mint`; idempotent under `content_hash` dedup, identical to the
  atomize write path.

**The Source id is content-addressed via a dedicated template** (the load-bearing
fix). It must **not** reuse the generic `source:
"atelier:source:{created_at}|{discriminator}"` template — its `{created_at}`
component is per-capture wall-clock and would reintroduce exactly the variance we
are eliminating. Instead, add a dedicated content-only template mirroring the
YouTube precedent (`atelier:youtube:{video_id}`, which likewise bypasses the
generic template):

```
operational: "atelier:operational:{content_hash}"   # content_hash = sha256(normalized body/statement); NO created_at
```

Same lesson → same `content_hash` → same Source id → same `derived_from` → same
claim id. This preserves `capture`'s ledger-less cross-session dedup, which the
constant anchor provided today. The frontmatter `created_at` stays a real
timestamp — it is simply not part of *this* id template. (Verified:
`structure.yaml:155` generic `source` template carries `{created_at}` verbatim
(`resolver.py:258-263`, not in `_NORMALIZED_PARTS`), so reusing it would
reintroduce per-capture variance; `structure.yaml:139-140` `youtube:{video_id}`
and `claude:{body_sha}` are the content-only precedents to follow.)

The two writers become **born-as-Source, then mint** (both converge on one mint
fn):
- **`capture`** writes a content-addressed operational Source in
  `raw/operational/`, carrying session metadata (`session_id` / `working_dir` /
  `attributed_to` / `hook` / `captured_at`) as **first-class Source provenance**
  (these fields are *not* in the id, so re-capture still dedups; the surviving
  Source reflects one capture's metadata, exactly as one claim does today). Then
  mints the 1:1 Claim. **The no-substance gate runs BEFORE the Source write**
  (fixes N1 — otherwise a substanceless capture would leave a content-free Source
  that P10 was meant to prevent and that the atomize nudge would flag forever).
- **`absorb`** writes a per-memory content-addressed Source carrying
  `source_path` / `claude_memory_type`; the `sha256(body)` dedup ledger is
  unchanged. Then mints. `type → ac_status` mapping unchanged.

## 5. Migration & sequencing (gated)

**Forward-only. Existing anchor-hung Claims are grandfathered — never rewritten.**
This is a deliberate consequence of the id derivation: because
`claim_id = f(statement, derived_from)`, repointing a legacy Claim's
`derived_from` off the anchor would necessarily change its id, orphaning any
inbound `links.to` / `derived_from` (e.g. a dreamed principle referencing it) and
making "entry_id conserved" a contradiction. So we do **not** touch legacy Claims:
the anchor is **frozen** (no new attachments), legacy Claims keep resolving and
recalling exactly as today. Uniform lineage on *new* operational memory is the
win; historical claims keep their flat provenance as a bounded, harmless legacy.

```
M0  This RFC — ratify the front-door invariant, content-addressed Source,
    mint dispatch, and forward-only migration.                          [gate: approval]
M1  Additive schema + engine: `operational` in source.domain + entity.in_scheme;
    `mint` in generated_by; content-addressed operational Source id;
    deterministic-mint fn + tests. New path unused by writers → 0 behavior change. [gate: suite green; enums additive; mint fn LLM-free; same lesson → same Source id (idempotency test)]
M2  Repoint capture.py + absorb_claude.py: born-as-Source (raw/operational/,
    content-addressed) → mint. no-substance gate BEFORE Source write;
    non-blocking capture contract + absorb ledger preserved.            [gate: capture/absorb round-trip; re-capture of same lesson dedups to one claim; absorb ledger intact; each new Claim derived_from its own Source; acceptance criteria unchanged — tied_to_event/has_project_tag still resolve on minted claims]
M3  Freeze the anchor (writers no longer attach to it) + remove
    raw/knowledge/_new/. Legacy anchor-hung Claims untouched.           [gate: 0 NEW Claims on the anchor; legacy Claims still resolve; no inbound reference dangles; reindex clean]
M4  Independent review → doctor green → merge.                          [gate: doctor v7-green; full suite green]
```

Cutover note (bounded, disclosed): a lesson captured *both* before and after M2
via `capture` (which has no ledger) could produce one legacy anchor-hung claim
*and* one new content-addressed claim — a one-time, bounded dedup gap. `absorb`
is protected by its body-hash ledger and does not double. This is accepted rather
than mitigated with a risky rewrite; a later optional pass (M3b, out of scope
here) could reconcile legacy claims if ever deemed worth the link-rewrite risk.

## 6. Freezing P10's anchor (its original rationale, honored)

P10 introduced the single anchor to avoid **content-free per-learning Source
stubs**. That concern is met, not ignored: the new per-item Source is **not an
empty stub** — it carries real content (the absorbed memory body, or the captured
observation, whose emptiness is rejected by the substance gate *before* the write,
§4/N1) *and* its provenance. So we get what P10 wanted (no content-free stubs)
*and* real per-Claim lineage for new memory. P10 is **frozen, not deleted**: the
anchor remains as the legacy root for pre-0007 claims; nothing new attaches.

## 7. Risks & mitigations

- **Content-addressed Source collision across genuinely-different lessons with
  identical normalized text.** Two distinct captures whose normalized bodies are
  byte-equal collapse to one Source/Claim — but that is *correct* dedup (they are
  the same assertion), and is exactly today's behavior via the constant anchor.
  No regression.
- **Cutover dedup gap (capture only).** Bounded and disclosed (§5). `absorb`
  ledger-protected.
- **Hard rule #7 (source read-only).** Absorb writes Sources **inside the vault
  only**; `~/.claude/projects/**` originals never touched; ledger prevents
  re-import.
- **Mis-routed raw material in the operational lane.** Dispatch trusts the lane
  unconditionally, so a long-bodied `raw/operational/` Source would be minted 1:1
  (under-extracted) with nothing to catch it. Mitigation: a cheap defensive lint
  (`domain == operational` and `word_count > N` → warn), restoring the safety net
  the dropped `atomic` marker's lint used to provide. Non-blocking (the lane is
  writer-controlled).
- **Big-bang on a live path.** M1 inert (unused code); M2 switches writers behind
  preserved contracts; M3 is a freeze + a directory removal, not a data rewrite.
  Each independently gated and reversible.

## 8. Non-goals

- **Not** forcing domain-known input through `inbox`. Front-door unification is at
  the **`raw/` layer**, not the `inbox` directory; `inbox` stays
  domain-*undetermined* only.
- **No new LLM step.** Mint is deterministic; zero model calls added to the write
  path (consistent with RFC 0003 / RFC 0005 §7.2).
- **No rewrite of legacy anchor-hung Claims** (forward-only; §5). Full anchor
  deletion + legacy re-mint is an explicitly deferred, separately-gated option.
- **No change to the surfacing ladder / promote / dream.** Orthogonal.
- **Not migrating the dormant mobile `atelier_capture`.** It already lands in
  `raw/inbox/` as a Source (correct shape; still schema-4). v7 migration tracked
  separately, gated behind mobile activation (v0.3).

## 9. Verification (acceptance)

1. Every write path produces a **per-item Source** in `raw/`; grep shows no
   writer attaching a *new* Claim to the anchor.
2. `operational` is valid on `source.domain` **and** `entity.in_scheme`;
   validation passes; operational Sources live in `raw/operational/`.
3. **Idempotency preserved** — the same lesson captured in two sessions yields
   **one** Source and **one** Claim (content-addressed id test); this is the test
   that proves the anchor's dedup role transferred cleanly.
4. The capture/absorb path issues **no model call** (mint is LLM-free); measured
   token cost unchanged vs today.
5. Legacy anchor-hung Claims still resolve by id after M3 (grandfather intact); no
   inbound `links.to` / `derived_from` dangles (referential-integrity check).
6. `raw/knowledge/_new/` removed; RFC 0005 §3.2 honored physically.
7. **Enumeration-bypass check** — a naive new stage that scans "every Source" (or
   "every Claim") sees operational alongside knowledge/personal with no
   domain-specific branch. (Filtering-asymmetry bugs are explicitly *not* claimed
   fixed — §1.)
8. `doctor` v7-green; full suite green (was 573).
