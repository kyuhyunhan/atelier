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
