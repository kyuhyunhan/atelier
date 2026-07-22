# RFC 0008 — Absorb lifecycle: discovery, supersession, depth, safety

| | |
|---|---|
| **Status** | Draft (proposed 2026-07-22) |
| **Scope** | the `absorb` ingest path (`~/.claude/projects/*/memory/` → vault): a fourth nudge (**absorb**), a path-indexed dedup ledger with **supersede-on-path**, the depth policy for long memories (mint stays 1:1; deep atomize is additive + human-gated), and sensitivity defaults + a PII demotion gate at ingest |
| **Builds on** | RFC 0005 (atomic graph; nudge posture §7.2), RFC 0007 (born-as-Source + deterministic mint — absorb already rides this path) |
| **Revises** | nothing structurally. The mint path, the content-addressed `raw/operational/` Source, and the body-hash ledger all stand. This RFC adds the *perimeter*: when absorb runs, what happens when upstream edits, how depth is handled, and what lands private. |
| **Schema** | no new node class, no new enum values. Ledger file format gains a `by_path` index (backward-compatible rebuild). Config gains `learnings.absorb.*` keys. |

---

## 1. Summary & thesis

`absorb` (RFC 0007 M2) is mechanically sound: each Claude Code memory lands as
its own content-addressed operational Source in `raw/operational/`, a
deterministic LLM-free mint derives the Claim, and a body-hash ledger makes
re-runs no-ops. What is missing is everything *around* the mechanism:

1. **Discovery** — nothing tells the human that other sessions have accrued
   memories. The siphon exists but nothing says "pull it." Learnings rot
   silently in `~/.claude`.
2. **Supersession** — when an upstream memory file is *edited*, its new body
   hash mints a fresh Claim and the old one lingers as an unmarked orphan.
3. **Depth** — the design assumed Claude memories follow their own "one file,
   one fact" convention. Measurement (§2) says they do not: most are multi-fact
   documents. The 1:1 mint must be re-justified or re-routed.
4. **Safety** — every absorbed claim lands `sensitivity: public` today, including
   `type: user` memories (who the user *is*) and verbatim bodies from work
   repos. No PII pass runs at ingest.

> **Thesis.** Extend absorb with **zero new judgement machinery**. Each gap is
> closed by re-using an existing pattern: discovery = the existing nudge shape
> (a derived count, human-pulled); supersession = the existing hash comparison,
> extended with a path index (a deterministic fact, not a judgement);
> depth = the existing additive-atomize semantics on top of a minted Source;
> safety = the existing PII pattern file, applied as a *demotion* (never a
> block). If a milestone here needs a new gate or a new LLM call, it is
> mis-designed.

## 2. Current state & measurement (2026-07-22)

Live census of `~/.claude/projects/*/memory/*.md` vs the vault ledger:

| Metric | Value |
|---|---|
| memory files (excl. `MEMORY.md` indexes) | 61 |
| absorbed (in ledger) | 37 |
| **unabsorbed backlog** | **25** |
| body word count — min / median / p90 / max | 94 / 253 / 471 / 1082 |
| unabsorbed bodies > 120 words | 22 of 25 (88%) |

Two findings drive this RFC:

- **The backlog is real and invisible.** 25 memories across ~6 projects are
  waiting, and nothing surfaces that number anywhere. (M1)
- **The "already atomic" premise is false in practice.** Claude Code's memory
  instructions say one file = one fact, but real memories are multi-fact
  documents (median 253 words). However — nearly every memory (57 of 61; the
  rest fall back to the file name) carries a *curated one-line `description`*,
  and the mint's `statement` comes from that description, not the body. So the minted Claim **is** atomic; it is the
  body's *additional* facts that stay claim-less. This reframes the depth
  question (§5): the mint is not wrong, it is *shallow*.

## 3. M1 — Discovery: the absorb nudge

A fourth nudge kind, `absorb`, alongside `atomize` / `promote` / `dream`
(RFC 0005 §7 unified surface).

**The derived state.** A memory is *unabsorbed* iff its normalized body sha256
is absent from the vault ledger:

```
unabsorbed_count = |{ m ∈ ~/.claude/projects/*/memory/*.md : sha(m) ∉ ledger }|
```

Deterministic, read-only, LLM-free. Cost at session start: one directory walk +
one sha256 per file (61 files today — negligible), same posture as the atomize
scan. `MEMORY.md` indexes are skipped, as in `absorb._iter_memories`.

**Surface.** `runtime/service/learnings/absorb_claude.py` gains
`unabsorbed_count()` + `nudge_info()` in the standard `{due, count, short,
long}` shape; `runtime/service/nudges.py` gains a tolerant `_absorb_nudge()`
wrapper; `all_nudges` order becomes `absorb → atomize → promote → dream`
(lifecycle order: ingest before atomize). Threshold key:
`learnings.absorb.nudge_after_memories` (default 1).

**Posture.** Human-pulled, never cron. Absorb is the only ingest that reads
outside the vault, and it auto-passes `feedback`/`reference` claims — unattended
runs would accrue unreviewed `passed` claims. The nudge says *what is waiting*;
the human runs `atelier_absorb_claude_memory`.

**Gate.** Nudge fires on a synthetic unabsorbed memory in a temp
`source_root`; goes quiet after absorb; a failing probe yields a not-due Nudge
(tolerance test); `all_nudges` returns four kinds.

## 4. M2 — Supersession: path-indexed ledger

**Problem.** The ledger is keyed by body hash only. An upstream *edit* is
indistinguishable from a *new* memory: the new hash mints a new Claim, the old
Claim stays live, and recall can serve both (one stale).

**Mechanism.** "Same file path, new hash" is a deterministic *fact* meaning
"this memory was revised" — no judgement needed. The ledger gains a `by_path`
index. Its key is **machine-independent** — `<encoded-project-dir>/<filename>`,
not the absolute path — because the ledger is git-tracked precisely so dedup is
consistent across machines; an absolute `~/.claude/...` key would never match on
a second machine and supersession would silently not fire there:

```json
{
  "by_sha":  { "<sha>": { "source_path": "…", "absorbed_at": "…", "claim_id": "…", … } },
  "by_path": { "<encoded-project-dir>/<filename>": "<latest sha>" }
}
```

- Migration: a legacy flat ledger is rebuilt in place on first load
  (`by_sha` = old object; `by_path` derived from each entry's `source_path`).
  Backward-compatible, one-time, no data loss.
- `by_sha` entries now record the minted `claim_id` (returned by
  `mint_operational_claim`). The 37 legacy entries lack it, but because
  claim_id is a deterministic function of the statement (= the description),
  legacy claim_ids are **recomputable offline** from ledger + vault — an
  optional one-time backfill, no re-ingestion needed. Until backfilled, legacy
  entries simply cannot supersede (forward-only, same posture as RFC 0007).

**The content-addressing constraint (what a naive design gets wrong).** Claim
id = f(statement, derived_from) and the operational Source id = f(statement)
alone. The statement is the memory's `description`. So an upstream **body edit
that keeps the description — the common case** — yields a new body sha but the
*same* claim_id and *same* Source id: the naive "retract old, mint new" would
retract the claim it just minted, and the idempotent Source write would silently
drop the revised body. Supersession must therefore branch on whether the
*statement* changed, not just the body:

**On absorb of a first-seen hash whose path is known** (previous sha + claim_id
resolved via `by_path` → `by_sha`):

1. **compute, don't write**: `new_claim_id` is a pure function of the statement
   (`entry_id("claim", statement, source_id)`), so derive it *before* any mint
   and branch on it. Minting first would be destructive: the claim write is an
   unconditional atomic overwrite at the content-addressed path, so a same-id
   re-mint would rebuild the frontmatter from absorb defaults — silently
   demoting a promoted claim back to `surfacing: query`, un-retracting a
   curator-retracted one, and wiping `links`/history. Lifecycle state lives in
   field transitions (RFC 0005); a re-absorb must never reset them.
2. **Statement unchanged** (`new_claim_id == old_claim_id`) — a *body-only
   revision*. **The Claim file is not written at all** — its statement is
   unchanged and its lifecycle fields (surfacing, `ac_status`, `accepted_at`,
   links) are preserved untouched. Only the Source's **body is refreshed in
   place** (same Source id; `body_sha` in its frontmatter updated, `revised_at`
   stamped). The Source's id is content-addressed by *statement*, so a body
   refresh does not break addressing; it is copy-semantics fidelity — the vault
   mirror tracks the upstream revision instead of silently keeping a stale
   body. Claims previously deep-atomized (M3) from the old body remain
   (additive; the curator retracts any the revision invalidated).
3. **Statement changed** (`new_claim_id != old_claim_id`) — a real
   supersession: mint the new Source + Claim as normal, carrying the `refines →
   old_claim_id` link on the **new** claim (via the mint's `extra`, where links
   belong — not on the retract call). Guard first: if any *other* `by_path`
   entry still resolves to `old_claim_id` (two memory files sharing one
   description collapse onto one content-addressed claim), **skip the retract**
   — the claim is still owned by a live path — and only link. Otherwise retract
   the old claim via the existing archive machinery — **`ac_status: retracted`**
   (`set_ac_status`, which also stamps `archived_at` + `archive_reason`) — so it
   exits promote eligibility through the same field every other retraction uses.
4. update `by_path`.

**Upstream deletion** is deliberately *not* mirrored: absorb is copy-semantics
(CLAUDE.md hard rule #7); a memory deleted in `~/.claude` leaves its vault claim
intact — the vault is the durable archive, the source tree is working memory.

**Known corner (accepted):** an upstream *revert* to a previously-absorbed body
is invisible — the old sha is already in `by_sha`, so dedup no-ops and the vault
Source keeps the newer body. Rare, self-healing on the next genuine revision,
and detectable via `by_path` if it ever matters; not worth special-casing now.

**Gate.** Four tests: (a) body-only revision → same claim **byte-identical**
(surfacing/`ac_status`/links untouched — pin this against a pre-promoted
fixture), Source body refreshed; (b) description revision → old claim
`ac_status: retracted` + `archived_at`, new claim `refines` old, `by_path` at
v2; (c) shared-description guard → retract skipped while a second path owns the
claim. Plus: ledger migration (flat → indexed, counts preserved, keys
machine-independent) and re-run stays a no-op.

## 5. M3 — Depth: mint stays 1:1; deep atomize is additive + human-gated

**The measurement killed the obvious design.** The draft idea was routing:
`word_count > threshold → skip mint, write Source only`, letting the un-atomized
derived state pull long memories into the atomize backlog. With 88% of the
backlog over any sane threshold, that routing would (a) push nearly every absorb
into LLM atomization — exactly the cost absorb exists to avoid — and (b) throw
away the *free, already-curated* claim sitting in each memory's `description`.

**Decision.** Keep the deterministic 1:1 mint for **every** memory, regardless
of length. The minted claim (statement = the curated description) is the
memory's atomic headline; the full body remains on the Source — indexed,
FTS-searchable, recallable. Depth extraction is then **optional and additive**:

- `atelier-atomize` on an already-minted operational Source is *legal* and
  purely additive — new Claims `derived_from` the same Source alongside the
  minted one. Content-addressing makes this idempotent; nothing is re-written.
- **Deep-atomized claims inherit the Source's sensitivity.** The atomize write
  path derives sensitivity from *domain* only (`personal` → private, else
  public); an M4-demoted private operational Source must not have public claims
  minted off its body. The additive path takes
  `max(domain default, source sensitivity)` — the same tighten-only posture
  dream already enforces (sensitivity may escalate toward private, never
  relax toward public).
- It is **human-directed only**: no nudge counts "shallowly atomized" sources,
  because shallow-vs-deep is a judgement about *worth*, not a derived fact.
  The human deep-atomizes a memory when its body has proven to matter.
- The atomize skill's scope note is updated to name operational Sources as an
  additive, on-request target (it currently scopes to knowledge/personal).

This preserves the RFC 0007 economics: the deterministic lane stays LLM-free;
the generative lane stays gated.

**Gate.** Test: atomize-write on a minted operational Source adds claims
without touching the mint claim or the Source; `unatomized_count` never counts
a minted Source (regression guard — already true, now pinned).

## 6. M4 — Safety: sensitivity defaults + PII demotion

Two changes at the absorb boundary, both demotions, never blocks:

1. **`type: user` → `sensitivity: private`.** A `user` memory describes who the
   user is (role, preferences, traits). Today it lands public/pending; it
   should land private/pending — recallable on explicit query, never
   proactively pushed, never promote-eligible (the promote gate already
   requires public). `feedback`/`reference`/`project` stay public.
2. **PII pattern pass.** The body of each first-seen memory is scanned against
   `~/.atelier/pii_patterns.txt` — the *same* pattern file the pre-commit guard
   uses (one vocabulary, two enforcement points). On a match: mint proceeds,
   but Claim *and* Source land `sensitivity: private` with a
   `pii_flag: true` marker for later curation. Absent pattern file → pass is a
   no-op (matches the guard's behavior).

Rationale for demote-not-block: blocking loses data and breaks the "absorb is
a faithful copy" contract; demotion narrows the surface to explicit on-query
while keeping the copy complete. Purge, if ever needed, is the existing
vault-only purge path (hard rule #7 — the source is never touched).

**Gate.** `type: user` fixture lands private; a body matching a temp pattern
file lands private + flagged; both remain deduped on re-run; no pattern file →
behavior identical to today.

## 7. Sequencing & non-goals

**Order: M1 → M4 → M2 → M3.** M1 unblocks the whole loop (nothing else matters
if absorb never runs). M4 is next because every absorb run *before* it lands
`user` memories public — safety before volume. M2 matters as upstream memories
start evolving. M3 is a docs/scope change plus one pinned test, schedulable any
time. To avoid M1→M2 rework, M1 reads the ledger through a **single accessor**
(`is_absorbed(sha)`) so M2's `by_sha` nesting changes one function, not every
call site.

**Non-goals:**

- **No cron / autosync absorb.** Human-pulled by design (§3 posture).
- **No reverse writes.** `~/.claude/projects/*/memory/**` stays strictly
  read-only (hard rule #7). Nothing here weakens copy-semantics.
- **No `MEMORY.md` absorption.** The index is navigation, not content.
- **No retroactive re-absorb of the 37 ledgered memories.** Grandfathered
  as-is; M2's supersession applies once a `claim_id` exists — for legacy
  entries, via the optional offline backfill (§4), never via re-ingestion.
- **No cross-session semantic dedup** (two memories saying the same thing in
  different words). That is dream/consolidation's job, downstream.

## 8. Risks

- **Nudge scan touches `~/.claude` at session start.** Read-only and cheap
  today (61 files), but growth is unbounded. Mitigation: the count short-circuits
  at the nudge threshold display cap; if the walk ever measurably slows
  bootstrap, cache `(mtime, sha)` per path in the ledger — an optimization, not
  a semantic change.
- **Supersede assumes path identity = memory identity.** A memory *renamed*
  upstream reads as new (old claim never retracted) — same failure class as
  today, no worse; accepted.
- **Body refresh softens Source immutability.** §4's body-only revision updates
  an operational Source's body in place. This is scoped strictly: only the
  absorb path, only for a Source whose upstream file revised, only the body +
  `body_sha`/`revised_at` — the statement (hence the id) never changes. The
  alternative — an immutable stale mirror silently diverging from its upstream
  — is the worse violation of copy-semantics.
- **PII patterns are user-maintained.** An empty/missing pattern file silently
  disables the pass. Accepted: identical trust model as the pre-commit guard.
- **`description` quality bounds mint quality.** A memory with a weak
  description mints a weak statement. Accepted: the body survives on the
  Source; M3's additive deep-atomize is the recovery path.
