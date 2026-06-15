# RFC 0004 — Deterministic session-end capture

Status: **Phase 1 implemented** (capture wiring). Phase 2 (curation/backfill) proposed.

## Problem

atelier's recall (read) half is wired deterministically via the `UserPromptSubmit`
hook and works. The capture (write) half is **not** wired to a hook — it depends
on the live agent choosing to call `atelier_learning_capture` mid-session. The
old `Stop`/`SessionEnd` adapter (`capture-learning.sh`) was deprecated (PR-37)
because blind hook captures carry no `why` and are rejected by the capture
substance gate (`empty-why`). Net effect: durable material at session boundaries
is silently lost, and the learning loop is half-wired by design.

## Decision

Move the quality gate from **capture-time** to **curation-time** for the hook
path — which restores the system's stated contract ("capture is permissive;
acceptance criteria are checked at promotion time"). The `empty-why` gate is the
one place that contradicted that contract.

- **Phase 1 (this RFC, implemented):** fire capture on `SessionEnd` **and**
  `PreCompact` with `require_why=false`. The candidate lands raw (observation =
  transcript tail, no why). The `no-substance` gate still drops empty/trivial
  sessions.
- **Phase 2 (proposed):** an LLM curation pass over no-why candidates that
  **proposes** a backfilled `why` for human approval (per the "don't ghostwrite
  judgment" principle) and rejects the rest. Runs before/with the dream cycle.

## Phase 1 design

No change to the capture handler or the substance gate. The engine already:
- builds the observation from `_extract_transcript_tail(transcript_path)` when no
  `observation` is supplied, and
- accepts `require_why` (the running server booted with this signature).

Three changes only:

1. **`runtime/service/mcp_call.py`** — add a `--require_why` flag (coerces
   `false`/`true` → bool) and, **scoped to `atelier_learning_capture`**,
   whitelist forwarded stdin keys to the capture tool's real params. Claude
   Code's hook payload carries envelope keys (`cwd`, `hook_event_name`,
   `transcript`, `trigger`) that are not capture params and would break
   signature binding.
2. **`scripts/hooks/session-capture.sh`** (new) — forwards the hook payload with
   `--require_why false`. Supersedes the deprecated `capture-learning.sh` (left
   in place as historical reference). Always exits 0.
3. **`~/.claude/settings.json`** — register the adapter on `SessionEnd` and
   `PreCompact`.

No `serve` restart required: `atelier-mcp-call` is a short-lived client process,
re-executed per hook call; the running server already supports `require_why`.

## Data flow

```
SessionEnd / PreCompact
  → session-capture.sh  (require_why=false, transcript_path from payload)
    → atelier_learning_capture
      → obs = transcript tail (last ~3 turns, ~600 chars)
        → no-substance gate: empty/trivial → skipped
        → otherwise → candidate written (no why)
          → [phase 2] curation: propose why / reject
```

## Acceptance criteria (phase 1)

1. A non-trivial session end writes a candidate with `hook: SessionEnd`,
   observation = transcript tail, no why — not rejected.
2. A trivial/empty session writes nothing (no-substance gate holds).
3. `PreCompact` on a long session writes a candidate.
4. The statusline heartbeat shows `⟲ capture` when it fires.
5. `atelier_learning_review_pending` lists the new no-why candidates.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Candidate-space pollution | `candidate_retention_days: 60`; phase-2 curation rejects aggressively |
| Thin tail misses earlier lessons | accepted for phase 1; phase 2 may extract over the full transcript |
| `PreCompact` fires repeatedly → dupes | accepted for phase 1; content-hash dedup is a phase-1.5 follow-up |

## Rollback

Remove the two `settings.json` entries (instant); revert `mcp_call.py`. The
adapter becomes inert.
