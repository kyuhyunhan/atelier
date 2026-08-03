# Contributing to atelier

Thanks for looking. atelier is small and opinionated, and a few of its rules
are unusual enough that reading this first will save your PR a round-trip.

## Setup

```bash
git clone https://github.com/<your-fork>/atelier && cd atelier
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,serve,semantic]"
./scripts/setup                    # installs the pre-commit guard
ATELIER_EMBED=off python3 -m pytest -q     # the whole suite, deterministic
```

`ATELIER_EMBED=off` keeps tests lexical (no model download, no provider).
Point a throwaway config at `examples/vault-seed/` to try the system end to
end — its README is a five-minute tour.

## The rules that are different here

These come from `CLAUDE.md` (the canonical text — read it). The ones
contributors trip on:

1. **Never quote live vault content — not even as an example.** Engine code,
   tests, docs, fixtures, and commit messages must use synthetic placeholders
   (`홍길동`-class fictional names, invented titles, invented paths). Public
   figures are fine as *knowledge subjects*; people from anyone's personal
   scheme never are. The pre-commit guard catches a static core; this rule
   closes the long tail, and reviews enforce it as a blocker.
2. **Markdown is truth; the DB is a projection.** Nothing may write SQLite
   directly — all state flows through markdown and `atelier reindex`. A PR
   that makes the DB a source of truth for anything will be declined.
3. **Schema is data, not code.** Page types, enums, and structure rules live
   in `schema/data/*.yaml`; the runtime reads them. Hard-coding a schema
   decision in Python is a bug even when it works.
4. **History is immutable.** RFCs, `CHANGELOG` history, and `docs/_archive/`
   describe what was true when written. Don't "fix" them during renames or
   refactors — new truth gets a new entry, old records keep the old names.
5. **A design doc is not a backlog.** If your PR declares future work (in an
   RFC, a doc, a TODO), it either ships in a tracked issue or carries a
   formal disposition. Items that exist only as prose are treated as defects
   by the next audit.

## What a PR needs

- **Tests, and honest ones.** New guards get *reverse-tested*: show (in the
  PR description) that deliberately breaking the thing makes the test fail.
  A test that only ever passed is unproven — several assertions in this
  repo's history were vacuous until a reverse-test exposed them.
- **The suite green**: `ATELIER_EMBED=off python3 -m pytest -q`.
- **A `CHANGELOG.md` entry** under `[Unreleased]` for anything an adopter or
  maintainer would notice. State measured facts, not intentions — entries
  here have been blocked in review for a single overclaiming sentence.
- **No behavior smuggled into a rename/cleanup PR**, and no cleanup smuggled
  into a behavior PR. Small, single-subject PRs review fast.
- CI runs two required checks: a structural guard (large-file / bulk-export
  protection) and the full suite. Both must pass; neither can be waived.

Commit authorship: use your own identity. (The maintainer's `gorae` identity
is a documented exception for the maintainer only — don't imitate it, and
never add `Co-Authored-By` trailers for tools.)

## How changes are reviewed

Substantive PRs get an independent review against the diff, findings tagged
`[MUST]` / `[SHOULD]` / `[NIT]` / `[Q]`. The bar to merge: zero MUSTs, every
SHOULD either taken or explicitly declined with a reason. Reviewers here run
what the PR claims (demo commands, metrics, reverse-tests) rather than
reading along — write your PR description so that's easy.

Larger designs go through an RFC in `docs/rfc/` (see 0001–0009 for the
house style: metadata table, phased gates, explicit non-goals, and a status
line that gets a disposition when the work closes).

## Where things are

| Need | Path |
|---|---|
| Entry-point rules (canonical) | `CLAUDE.md` |
| Architecture overview | `docs/ARCHITECTURE.md` |
| Adoption / setup | `docs/ADOPTING.md` |
| Daily-use contract | `docs/USING.md` |
| Schema (data) | `schema/data/*.yaml` |
| Demo vault | `examples/vault-seed/` |
| Release history | `CHANGELOG.md` |
