# migrate-schema-v3-to-v4

One-shot frontmatter migration from atelier Schema v3 (proto-engine era)
to Schema v4 (atelier engine).

The script is **idempotent**: re-running it is a no-op once the meta
marker `schema_migration_v3_to_v4` is recorded in the SQLite cache. Pass
`--force` to bypass.

## What it changes

For every `*.md` file under the configured space root:

- `schema_version: <not 4>` → `schema_version: 4`
- `entry_id: PENDING` or missing → stable `uuid5(DNS, "atelier:<relpath>")`
- nothing else is touched (no body edits, no other frontmatter keys)

The script **never commits**. The operator reviews the resulting diff in
the content repo and commits there.

## Usage

```bash
# 1) Dry-run (default) — prints summary by directory; no writes.
python -m scripts.migrate_schema_v3_to_v4.migrate \
    --role librarian-territory

# 2) Apply — writes frontmatter, records the meta marker. Refuses if the
#    target directory has uncommitted changes.
python -m scripts.migrate_schema_v3_to_v4.migrate \
    --role librarian-territory --apply

# 3) Re-run after marker is set (e.g. you imported new v3 files).
python -m scripts.migrate_schema_v3_to_v4.migrate \
    --role librarian-territory --apply --force
```

## Safety

- The script refuses to apply when the target git tree is dirty
  (commit / stash first).
- The dry-run path opens no SQLite write transactions.
- The meta marker is set only after a successful apply.
- After migration, `atelier validate` rejects any file at
  `schema_version != 4`.
