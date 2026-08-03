---
entry_id: 190756cf-17c6-51e5-b35a-0f4c75513e15
schema_version: 7
kind: claim
created_at: '2026-07-01T09:00:00Z'
content_hash: a7ff4dca7eb88756
statement: Copying only the .db file of a WAL-mode SQLite database silently loses
  un-checkpointed transactions — copy the sidecars or use VACUUM INTO.
is_about:
- f04bfa77-f7b9-5d86-b40b-8aef694a7425
derived_from:
- 51377b06-ce3d-5bd5-a290-64fc79752232
attributed_to: 홍길동
generated_by: atomize
surfacing: proactive
domain: knowledge
sensitivity: public
---

Copying only the `.db` file of a WAL-mode [[SQLite]] database silently loses un-checkpointed transactions — copy the sidecars or use `VACUUM INTO`.
