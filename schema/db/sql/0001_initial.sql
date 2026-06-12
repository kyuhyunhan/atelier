-- atelier DB v1 — SQLite + FTS5
-- Phase A baseline. Applied once on `atelier setup` (or first `atelier reindex`).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Core tables ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pages (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  slug         TEXT    NOT NULL UNIQUE,  -- space-relative path e.g. "wiki/entities/foo.md"
  space        TEXT    NOT NULL,         -- 'gorae' | 'workshop'
  page_type    TEXT    NOT NULL,         -- 'raw_source' | 'digest' | 'entity' | ...
  frontmatter  TEXT    NOT NULL,         -- JSON; original YAML parsed and stored as JSON
  content_hash TEXT    NOT NULL,         -- SHA-1 hex of raw file bytes (change detection)
  mtime        REAL    NOT NULL,         -- Unix timestamp (float) from filesystem
  -- Generated columns derived from frontmatter JSON
  title        TEXT    GENERATED ALWAYS AS (json_extract(frontmatter, '$.title'))      STORED,
  sensitivity  TEXT    GENERATED ALWAYS AS (json_extract(frontmatter, '$.sensitivity')) STORED,
  -- RFC 0003: provenance is a first-class single-valued field (where it came from:
  -- personal | knowledge | learning), projected like sensitivity. Added to the base
  -- CREATE TABLE (not via ALTER) because SQLite cannot ALTER-ADD a STORED generated
  -- column; the DB is a rebuildable projection, so `rm cache && reindex` applies it.
  provenance   TEXT    GENERATED ALWAYS AS (json_extract(frontmatter, '$.provenance'))  STORED,
  created      TEXT    GENERATED ALWAYS AS (
                          COALESCE(
                            json_extract(frontmatter, '$.created'),
                            json_extract(frontmatter, '$.created_at[0].value')
                          )) STORED
);

CREATE TABLE IF NOT EXISTS chunks (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  page_id      INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  position     INTEGER NOT NULL,   -- 0-based chunk index within page
  heading_path TEXT,               -- e.g. "Key Insights > Bullet 1" (nullable)
  text         TEXT    NOT NULL
);

-- FTS5 virtual table; content= keeps chunks_fts in sync with chunks.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  content=chunks,
  content_rowid=id,
  tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS links (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  from_page  INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  to_target  TEXT    NOT NULL,           -- raw wikilink string as written in markdown
  to_page_id INTEGER REFERENCES pages(id) ON DELETE SET NULL,  -- NULL = broken link
  link_type  TEXT    NOT NULL            -- 'wikilink' | 'gorae' | 'workshop' | 'concept'
);

CREATE TABLE IF NOT EXISTS entities (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_slug  TEXT    NOT NULL UNIQUE,  -- matches pages.slug for entity page
  aliases         TEXT    NOT NULL DEFAULT '[]',  -- JSON array of alternate names
  first_mention   TEXT,                           -- YYYY-MM period string
  confidence      REAL    NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Seed meta
-- DB schema_version jumps 4→6: '5' was the RFC 0001 frontmatter schema bump
-- (no DB-level change), '6' is RFC 0003's provenance column. Keep in lockstep
-- with diagnostics.EXPECTED_SCHEMA_VERSION.
INSERT OR IGNORE INTO meta VALUES ('schema_version',   '6');
INSERT OR IGNORE INTO meta VALUES ('atelier_db_version', '1');
INSERT OR IGNORE INTO meta VALUES ('created_at',       (SELECT datetime('now')));

-- ── Views ─────────────────────────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS backlinks_count AS
  SELECT to_page_id AS page_id,
         COUNT(*)   AS inbound_count
  FROM   links
  WHERE  to_page_id IS NOT NULL
  GROUP  BY to_page_id;

CREATE VIEW IF NOT EXISTS broken_links AS
  SELECT l.id,
         p.slug  AS from_slug,
         l.to_target
  FROM   links l
  JOIN   pages p ON p.id = l.from_page
  WHERE  l.to_page_id IS NULL;

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_pages_space      ON pages(space);
CREATE INDEX IF NOT EXISTS idx_pages_type       ON pages(page_type);
CREATE INDEX IF NOT EXISTS idx_pages_space_type ON pages(space, page_type);
CREATE INDEX IF NOT EXISTS idx_pages_provenance  ON pages(provenance);   -- RFC 0003 scope filter
CREATE INDEX IF NOT EXISTS idx_pages_sensitivity ON pages(sensitivity);  -- RFC 0003 scope filter
CREATE INDEX IF NOT EXISTS idx_links_from       ON links(from_page);
CREATE INDEX IF NOT EXISTS idx_links_to         ON links(to_page_id);
CREATE INDEX IF NOT EXISTS idx_chunks_page      ON chunks(page_id, position);

-- ── FTS5 content sync triggers ────────────────────────────────────────────────
-- Required when using content= mode: FTS index must be kept manually in sync.

CREATE TRIGGER IF NOT EXISTS chunks_ai
AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad
AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text)
  VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au
AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text)
  VALUES ('delete', old.id, old.text);
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
