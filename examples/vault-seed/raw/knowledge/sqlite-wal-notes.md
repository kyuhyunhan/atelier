---
entry_id: 57fc2598-3a37-5ddf-adfc-afd11bae8c4d
schema_version: 7
kind: source
created_at: '2026-07-01T09:00:00Z'
content_hash: sha256:6cfc8ab7657adf67d10e3ea68efec61dc9284518e66414ac251f3f6e6ee8ff8f
title: 'Notes: SQLite WAL mode'
sensitivity: public
domain: knowledge
attributed_to: 홍길동
---

# Notes: SQLite WAL mode

Write-Ahead Logging appends changes to a `-wal` sidecar instead of rewriting
pages in place. Readers keep reading the main file while a writer appends, so
readers never block writers. A **checkpoint** folds the WAL back into the main
database file.

One operational consequence: backing up a WAL-mode database by copying only the
`.db` file silently loses every un-checkpointed transaction — copy the `-wal`
and `-shm` sidecars too, or run `VACUUM INTO`.
