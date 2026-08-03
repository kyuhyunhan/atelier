# vault-seed — a five-minute tour vault

A tiny synthetic vault (15 pages, fictional content) so you can experience
atelier **before** you have accumulated any real memory. Every person in it
(홍길동 / "Gildong") is fictional; every note is invented for the demo.

## Try it

```bash
# 1. point a THROWAWAY config at a copy of this directory
cp -r examples/vault-seed /tmp/seed-vault
# in ~/.atelier/config.yaml set  vault.local: /tmp/seed-vault
#   (keep your real config elsewhere — this is a sandbox)

# 2. project it
atelier reindex --full

# 3. look around
atelier search "WAL"                 # lexical hit on the SQLite notes
atelier lint --show 10               # the seed lints clean
atelier nudges                       # one atomize nudge: the inbox capture
atelier doctor                       # D1–D8 on a healthy tiny vault
```

## What it demonstrates

| Concept | Where |
|---|---|
| raw → graph split (markdown is truth) | `raw/**` vs `graph/atomic/**` |
| the three node kinds (source / entity / claim) | `graph/atomic/seed-src-*`, `seed-ent-*`, `seed-clm-*` |
| surfacing tiers query ⊂ proactive ⊂ always | `seed-clm-wal-readers` / `seed-clm-wal-backup` / `seed-clm-backup-principle` |
| domains + the dev/life/full lens wall | knowledge / operational vs the `personal` diary claim |
| `sensitivity: private` never pushed proactively | `seed-clm-tomatoes` (derived from the diary) |
| the learning accept gate (pending → passed) | `seed-clm-learning-pending` vs `-passed` |
| an un-atomized capture raising a nudge | `raw/inbox/2026-07-01-clip-to-triage.md` |
| builder territory | `workshop/products/demo-widget/` |

The suite pins this seed: `tests/test_vault_seed.py` reindexes it and asserts
it validates, lints clean, and keeps one node of every kind and tier — so the
demo cannot silently rot as the schema evolves.
