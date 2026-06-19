-- 0003 — atomic knowledge graph, schema_version → 7 (RFC 0005 P6)
--
-- RFC 0005 turns the vault into an atomic knowledge graph: every node is a
-- `claim`, `entity`, or `source` markdown file carrying `kind` and
-- `schema_version: 7` in its frontmatter. The projection (runtime/index)
-- classifies those nodes by the `kind` FIELD (not the path) into pages.page_type
-- ∈ {claim, entity, source}, and embeds at CLAIM granularity (claims are the
-- unit of retrieval).
--
-- This is a PROJECTION-only schema bump — no new tables or columns are needed:
-- claim/entity/source nodes are ordinary `pages` rows distinguished by
-- page_type, and their chunks/links/embeddings reuse the existing tables. The
-- only DB-level change is the schema_version marker, which D1 checks.
--
-- 0001's seed uses INSERT OR IGNORE, so on a FRESH DB the value is already '7'.
-- On an EXISTING DB seeded at an older version this UPDATE forces it forward.
-- Idempotent: re-running on a DB already at 7 is a no-op.
UPDATE meta SET value = '7'
 WHERE key = 'schema_version' AND value <> '7';
