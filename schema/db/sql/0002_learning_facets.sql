-- 0002 — learning facets (RFC 0001)
--
-- Classification for learnings moves out of the directory path and into
-- INDEXED frontmatter facets resolved at query time. This side table is the
-- index. It mirrors the concept-edge pattern (reindex.py projects touches +
-- target_topic into the links table): a many-valued frontmatter list projected
-- into rows at reindex, deterministically, no LLM.
--
-- One table serves single- AND many-valued facets, so every facet filter is the
-- same predicate:
--   EXISTS (SELECT 1 FROM learning_facets
--           WHERE page_id = p.id AND kind = ? AND value = ?)
--
-- kind ∈ { 'project', 'aspect', 'topic', 'touches' }:
--   project  ← target_project (or project_hint)      single, project-local
--   aspect   ← aspect[]                               many,   project-local
--   topic    ← target_topic                          single, global (optional)
--   touches  ← touches[]                              many,   global concepts
--
-- Populated by runtime/index/reindex.py via clear-and-repopulate per page, so
-- a re-run is idempotent (same markdown → same rows). ON DELETE CASCADE keys
-- rows to pages.id (foreign_keys pragma is ON), so a page deletion in crawl
-- clears its facets automatically — same guarantee links/chunks already have.

CREATE TABLE IF NOT EXISTS learning_facets (
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  kind    TEXT    NOT NULL,
  value   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lf_kind_value ON learning_facets(kind, value);
CREATE INDEX IF NOT EXISTS idx_lf_page       ON learning_facets(page_id);
