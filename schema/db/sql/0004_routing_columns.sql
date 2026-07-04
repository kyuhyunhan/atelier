-- 0004 — indexed routing columns for lens filtering (RFC 0006 ② / issue #6).
--
-- The lens (RFC 0006 ③) filters on (kind, domain); consolidation and nudges
-- read ac_status / surfacing. These live inside the frontmatter JSON blob, so
-- filtering meant a scan. Promote them to indexed generated columns.
--
-- VIRTUAL, not STORED: SQLite only permits adding a generated column via
-- ALTER TABLE when it is VIRTUAL (STORED cannot be added post-hoc). The value is
-- computed from the frontmatter JSON on read; the index materializes it, so a
-- (kind, domain) filter uses the index rather than json_extract over every row.
--
-- Migrations run only on a fresh DB (see runtime/util/db._needs_migration); an
-- existing cache picks these up on `rm cache && atelier reindex` — safe, since
-- the DB is a rebuildable projection (hard rule #4). Correctness never depends
-- on these columns: readers may still json_extract; the columns are perf.
ALTER TABLE pages ADD COLUMN kind      TEXT GENERATED ALWAYS AS (json_extract(frontmatter, '$.kind'))      VIRTUAL;
ALTER TABLE pages ADD COLUMN domain    TEXT GENERATED ALWAYS AS (json_extract(frontmatter, '$.domain'))    VIRTUAL;
ALTER TABLE pages ADD COLUMN ac_status TEXT GENERATED ALWAYS AS (json_extract(frontmatter, '$.ac_status')) VIRTUAL;
ALTER TABLE pages ADD COLUMN surfacing TEXT GENERATED ALWAYS AS (json_extract(frontmatter, '$.surfacing')) VIRTUAL;

CREATE INDEX IF NOT EXISTS idx_pages_kind        ON pages(kind);
CREATE INDEX IF NOT EXISTS idx_pages_domain      ON pages(domain);
CREATE INDEX IF NOT EXISTS idx_pages_kind_domain ON pages(kind, domain);
CREATE INDEX IF NOT EXISTS idx_pages_ac_status   ON pages(ac_status);
