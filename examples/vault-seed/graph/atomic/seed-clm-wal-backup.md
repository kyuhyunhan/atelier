---
entry_id: 6ee70e2c-5350-5f61-b10a-09351d4a5ce8
schema_version: 7
kind: claim
created_at: '2026-07-01T09:00:00Z'
statement: Copying only the .db file of a WAL-mode SQLite database silently loses
  un-checkpointed transactions — copy the sidecars or use VACUUM INTO.
is_about:
- f04bfa77-f7b9-5d86-b40b-8aef694a7425
derived_from:
- 57fc2598-3a37-5ddf-adfc-afd11bae8c4d
attributed_to: 홍길동
generated_by: atomize
surfacing: proactive
domain: knowledge
sensitivity: public
content_hash: sha256:2f650137ec089724a3608aed7ba0fb746d3af42f8090f464fbf8d2176758d218
---

Copying only the `.db` file of a WAL-mode [[SQLite]] database silently loses un-checkpointed transactions — copy the sidecars or use `VACUUM INTO`.
