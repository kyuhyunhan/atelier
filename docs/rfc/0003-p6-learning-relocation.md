# RFC 0003 · P6 — relocate `learnings/` → `provenance/learning/`

**Status:** proposed · **Depends on:** RFC 0003 P0–P5 (merged) · **Method:** P1/GP1
lockstep (engine dual-path → vault `git mv` → cleanup)

## 1. Why

RFC 0003 §4 draws the target tree with learnings **under** provenance:

```
provenance/
  personal/ · knowledge/ · learning/   candidates/ · notes/
```

But §8 (migration) and the rollout plan only ever scheduled `raw/→provenance/`
and `wiki/→graph/`. The `learnings/`→`provenance/learning/` move was drawn as the
end-state yet **never decomposed into a phase** — it fell through the gap between
the §4 vision and the §8 steps. Result: a top level where `provenance/` sits next
to a sibling `learnings/`, which *reads* as "learnings are not provenance" while
the field `provenance: learning` says they are. In a markdown-truth, human-browsed
vault, that structural contradiction is a real defect, not cosmetics. P6 finishes
the §4 intent.

Counter-argument considered and rejected: "the field is the truth, the folder is
navigational, so the move is zero-value." Navigation *is* a value here — the vault
is browsed by a human; the folder must not assert something the field denies.

## 2. Decisions (settled)

- **Target name `provenance/learning/` (singular)** — matches the field value
  `provenance: learning` and the singular siblings `personal/`, `knowledge/`.
- **Substructure preserved:** `candidates/ notes/ accepted/ archived/ principles/`
  plus `log.md`, `criteria.yaml`, `.absorbed-from-claude/` move verbatim under it.
- **Stale principle evidence repointed** in this work: the 4 `principles/*.md`
  carry `evidence:` arrays pointing at the long-removed `learnings/accepted/by-topic/…`
  tree (already dangling). Repoint to surviving flat-store files / entry_ids.

## 3. Method — why lockstep, not one `git mv`

`learnings/` is the highest-coupling path in the repo: the literal is hard-coded in
`store.py`, `search.py`, `capture.py`, `new_doc.py`, `principles.py`, `indexes.py`,
`absorb_claude.py`, `criteria.py`, `review.py`, `reindex.py`, the schema
`path_patterns` (×5), config subtrees, lint, and the conftest fixture. There is **no
central root constant**. A bare `git mv` would dangle every one of these — exactly the
failure mode PR #16 (the missed `wiki/entities` write target) demonstrated. GP1 stayed
safe by teaching the engine **both** paths *before* the vault moved. P6 repeats it.

## 4. Phases

### P6-E1 — engine, dual-path (atelier PR; full suite green before vault is touched)
1. Add one helper `store.learning_root(vault)` → `provenance/learning` if it exists,
   else `learnings` (transition-resolving). Kill the scattered literal: every
   path-constructor routes through it.
2. Schema: add `provenance/learning/**` `path_pattern` variants beside each existing
   `learnings/**` in `learnings.overlay.yaml` (classification matches both).
3. `reindex` candidate-dir / prefix lists learn `provenance/learning/`.
4. Tests: assert `learnings/X` and `provenance/learning/X` classify **identically**,
   and `learning_root` resolves to the live tree.
   - **Gate:** suite green; reindex of the *current* (un-moved) vault is byte-identical
     in output (E1 is a functional no-op until the vault moves).

### P6-V1 — vault (gorae; after E1 merges)
1. `git mv learnings provenance/learning`; reindex.
2. Repoint the 4 principles' `evidence:` arrays to surviving targets.
   - **Gate:** `broken_links` view count does **not** increase; surfacing `newly_dark`
     empty; learning connectivity ≥ pre-move; `page_type='unknown'` count unchanged.

### P6-E2 — cleanup (atelier PR)
1. Drop the legacy `learnings/` aliases (helper + schema patterns).
2. Update docstrings (the ~12 sites), the `conftest.py` `workspace` fixture, and lint
   `path_pattern`s. Fold in issue #17's `wiki/→graph/` fixture+lint hygiene (same files).
3. Rewrite RFC 0003 §4: learnings shown **under** `provenance/learning/` as the
   completed state (delete the "aspirational" framing).
   - **Gate:** suite green; `grep -r 'learnings/'` returns only historical references
     in archived content, none in live code/schema/fixtures.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Missed path-constructor dangles a writer (PR #16 redux) | central `learning_root` helper = one place to change; grep-gate in E2 |
| Wikilinks `[[learnings/…]]` in bodies break post-move | dual-path link resolution in E1; repoint known refs (principles) in V1 |
| Config subtree path drift (per-machine `~/.atelier/config.yaml`) | audit + document the one-line config change in E1; ship `config/example.config.yaml` update |
| Connectivity/surfacing regression | the RFC 0002 `newly_dark` gate + connectivity re-measure at V1, as in GP3 |

## 6. Out of scope
- The `workshop/`→`product/` rename (separate, settled as a distinct space).
- Synthesis/digests archival (that is P5/GP5 — query-time synthesis).
