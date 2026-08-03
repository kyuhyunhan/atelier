# vault-seed — a five-minute tour vault

A tiny synthetic vault (14 content pages, fictional throughout) so you can
experience atelier **before** you have accumulated any real memory. Every
person in it (홍길동 / "Gildong") is fictional; every note is invented for the
demo. Regenerate with `python3 examples/generate_vault_seed.py` — every
`entry_id` is minted by the engine's own content-addressed templates.

## Try it

```bash
# 1. point a THROWAWAY config at a copy of this directory
cp -r examples/vault-seed /tmp/seed-vault
# in ~/.atelier/config.yaml set  vault.local: /tmp/seed-vault
#   (keep your real config elsewhere — this is a sandbox)

# 2. project it
atelier reindex --full

# 3. look around
atelier search "WAL"        # lexical hit on the SQLite notes
atelier nudges              # atomize is DUE: two Sources have no Claim yet
atelier lint --show 10      # no FAILs. L5 "orphan" WARNs on leaf claims are
                            # the vault's normal texture, not a defect —
                            # nothing links INTO a freshly minted claim
atelier doctor              # D1–D6 and D8 on a healthy tiny vault
```

## What it demonstrates

| Concept | Where |
|---|---|
| born-as-Source: raw files ARE the L1 Source nodes | every `raw/**` file carries `kind: source` |
| the three node kinds (source / entity / claim) | `raw/**` + `graph/atomic/seed-ent-*`, `seed-clm-*` |
| content-addressed ids (dedup keys, `structure.yaml` templates) | every `entry_id` = the engine's own `resolver.entry_id(...)` — pinned by the suite |
| surfacing tiers query ⊂ proactive ⊂ always | `seed-clm-wal-readers` / `seed-clm-wal-backup` / `seed-clm-backup-principle` |
| dream provenance (`refines`, non-empty `derived_from`) | `seed-clm-backup-principle` refines the wal-backup claim |
| mint provenance (claims derive from a session Source) | the two learnings ← `raw/operational/…gardening-session.md` |
| domains + the dev/life/full lens wall | knowledge / operational vs the `personal` diary claim |
| `sensitivity: private` never pushed proactively | `seed-clm-tomatoes` (derived from the private diary) |
| the learning accept gate (pending → passed) | `seed-clm-learning-pending` vs `-passed` |
| the atomize nudge counting un-derived Sources | the inbox capture + the soil note (both have no Claim) |
| entity wikilinks in claim bodies | `[[SQLite]]`, `[[홍길동]]` resolve via the entity alias index |
| builder territory | `workshop/products/demo-widget/` |

The suite pins this seed: `tests/test_vault_seed.py` reindexes it under the
documented single-vault config and asserts it validates, carries no lint
FAILs, keeps one node of every kind and tier, keeps the atomize nudge due,
and keeps every `entry_id` equal to what the engine's own id templates
produce — so the demo cannot silently rot as the schema evolves.
