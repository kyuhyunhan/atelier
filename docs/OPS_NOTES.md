# atelier — Operational Notes

This file accumulates branch-cases, surprises, and friction discovered during
the 7-day operational soak (Phase 9) and beyond. Each entry is a small datum;
patterns across entries inform v0.2 priorities.

---

## Soak runbook (7-day passive operation)

The Phase 9 acceptance gate is: **atelier survives 7 consecutive days of
normal use with no manual fixes required.** This is operated by the user,
not by a one-shot run.

### Daily checks (~2 min)

```bash
atelier doctor                       # all six diagnoses should be OK
atelier reindex                      # incremental; should finish <1s for gorae
atelier lint --space gorae --show 0  # report counts only
```

Drop anything surprising into this file under `## Findings`.

### Weekly checks (Sunday)

```bash
atelier reindex --full --space gorae
atelier lint --space gorae --fix     # apply L3/L4 fixes
atelier sync status                  # both spaces clean=True?
```

If any weekly check produces unexpected output, log it under `## Findings`
with date and command.

### What "no manual fix" means

- No editing the SQLite DB by hand.
- No manual frontmatter rewrites to satisfy linters.
- No `rm -rf cache/` to "reset" the index (`reindex --full` is fine; brute-force
  delete is not).

If any of those becomes necessary, log it as a `Phase 9 escalation` below.

---

## Findings

(append entries here during the soak)

### Template

```
### YYYY-MM-DD — short title

**Command**: `atelier ...`
**Observed**: what happened
**Expected**: what should have happened
**Workaround**: (optional)
**Class**: bug | UX | content | infra
**Action**: (defer to v0.2 / fix now / not actionable)
```

---

## v0.2 backlog (seeded from soak)

- (populated from `Action: defer to v0.2` entries above)
```

### 2026-07-19 — dream plan clusters are term-frequency artifacts

**Command**: `atelier dream` (158 proactive claims → 20 clusters)
**Observed**: clusters group by near-stopword shared terms (`file`, `user`,
`every`, `yaml`); the same claim appears in 5+ clusters; no cluster boundary
matched a semantic theme. Mechanically synthesizing all 20 would have minted
20 mushy always-claims. Real themes (3 of them) ran *across* cluster
boundaries; `dream.synthesize` accepts arbitrary `source_claim_ids`, so the
agent curated member subsets per theme instead — by design, but the cluster
quality bar makes the plan step mostly a candidate pool, not a plan.
**Expected**: clusters cohesive enough that one cluster ≈ one synthesis.
**Class**: UX
**Action**: defer to v0.2 (revisit clustering once RFC 0002 hybrid retrieval
lands — embedding-based grouping would beat shared-term counting)

### 2026-07-19 — dream plan latency vs the MCP client timeout

**Command**: `atelier-mcp-call atelier_dream_plan`
**Observed**: `dream.plan()` takes minutes on 158 proactive claims (each
member preview calls `find_claim_by_entry_id`, a full scan over ~4.3k claim
files → O(members × all-claims) file reads), while `mcp_call.py` hard-codes
a 15s read timeout — so the MCP surface for `atelier_dream_plan` cannot
complete over HTTP on a real vault. Worked around by calling
`dream.plan()` in-process.
**Expected**: plan completes within the tool-call timeout, or the timeout is
configurable.
**Class**: bug
**Action**: defer to v0.2 (index entry_id→path once per plan() call, or read
member previews from the projection instead of the filesystem)
