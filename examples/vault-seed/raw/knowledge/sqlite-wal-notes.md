---
schema_version: 4
entry_id: 75c5dc33-4d14-53fc-823d-79cf97534f13
sensitivity: public
created_at: '2026-07-01T09:00:00Z'
domain: knowledge
---

# Notes: SQLite WAL mode

Write-Ahead Logging appends changes to a `-wal` sidecar instead of rewriting
pages in place. Readers keep reading the main file while a writer appends, so
readers never block writers. A **checkpoint** folds the WAL back into the main
database file.

One operational consequence: backing up a WAL-mode database by copying only the
`.db` file silently loses every un-checkpointed transaction — copy the `-wal`
and `-shm` sidecars too, or run `VACUUM INTO`.
