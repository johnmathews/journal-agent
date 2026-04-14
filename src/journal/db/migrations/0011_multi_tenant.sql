-- Multi-tenant schema: users, sessions, API keys, and user_id scoping.
--
-- This migration converts the single-user journal to multi-user by:
-- 1. Creating users, user_sessions, and api_keys tables
-- 2. Seeding a placeholder admin user (id=1)
-- 3. Rebuilding entries with user_id (table rebuild for NOT NULL FK)
-- 4. Rebuilding entities with user_id (UNIQUE constraint change)
-- 5. Adding user_id to jobs (ALTER + UPDATE, app enforces NOT NULL)
-- 6. Assigning all existing data to the admin user

-- ── Step 1: Create auth tables ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    email                 TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name          TEXT    NOT NULL,
    password_hash         TEXT,
    is_admin              INTEGER NOT NULL DEFAULT 0,
    is_active             INTEGER NOT NULL DEFAULT 1,
    email_verified        INTEGER NOT NULL DEFAULT 0,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until          TEXT,
    created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id           TEXT    PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at   TEXT    NOT NULL,
    last_seen_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    user_agent   TEXT,
    ip_address   TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user    ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON user_sessions(expires_at);

CREATE TABLE IF NOT EXISTS api_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_prefix  TEXT    NOT NULL,
    key_hash    TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at  TEXT,
    last_used_at TEXT,
    revoked_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

-- ── Step 2: Seed admin user ─────────────────────────────────────────
-- Password will be set via CLI or first login. email_verified=1 so
-- the admin can log in immediately after setting a password.

INSERT INTO users (email, display_name, is_admin, email_verified)
VALUES ('admin@journal.local', 'Admin', 1, 1);

-- ── Step 3: Rebuild entries with user_id ────────────────────────────
-- SQLite cannot ALTER TABLE ADD COLUMN with NOT NULL + FK reference,
-- so we use the table-rebuild pattern (same as migrations 0002, 0007).

-- 3a. Drop FTS and all triggers on entries.
DROP TRIGGER IF EXISTS entries_ai;
DROP TRIGGER IF EXISTS entries_ad;
DROP TRIGGER IF EXISTS entries_au;
DROP TRIGGER IF EXISTS entries_entity_stale_on_final_text;
DROP TABLE IF EXISTS entries_fts;

-- 3b. Create replacement table WITH user_id.
CREATE TABLE entries_new (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                  INTEGER NOT NULL REFERENCES users(id),
    entry_date               TEXT    NOT NULL,
    source_type              TEXT    NOT NULL,
    raw_text                 TEXT    NOT NULL,
    word_count               INTEGER NOT NULL,
    language                 TEXT    DEFAULT 'en',
    created_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    final_text               TEXT,
    chunk_count              INTEGER NOT NULL DEFAULT 0,
    entity_extraction_stale  INTEGER NOT NULL DEFAULT 1,
    doubts_verified          INTEGER NOT NULL DEFAULT 0
);

-- 3c. Copy existing rows, assigning all to admin (id=1).
INSERT INTO entries_new (
    id, user_id, entry_date, source_type, raw_text, word_count, language,
    created_at, updated_at, final_text, chunk_count,
    entity_extraction_stale, doubts_verified
)
SELECT
    id, 1, entry_date, source_type, raw_text, word_count, language,
    created_at, updated_at, final_text, chunk_count,
    entity_extraction_stale, doubts_verified
FROM entries;

-- 3d. Drop old table and rename.
DROP TABLE entries;
ALTER TABLE entries_new RENAME TO entries;

-- 3e. Recreate indexes.
CREATE INDEX IF NOT EXISTS idx_entries_user      ON entries(user_id);
CREATE INDEX IF NOT EXISTS idx_entries_user_date ON entries(user_id, entry_date);
CREATE INDEX IF NOT EXISTS idx_entries_source    ON entries(source_type);

-- 3f. Recreate FTS on final_text (matching migration 0007).
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    final_text,
    content='entries',
    content_rowid='id',
    tokenize='porter unicode61'
);

INSERT INTO entries_fts(entries_fts) VALUES('rebuild');

-- 3g. Recreate FTS sync triggers on final_text.
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, final_text) VALUES (new.id, new.final_text);
END;

CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, final_text) VALUES ('delete', old.id, old.final_text);
END;

CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE OF final_text ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, final_text) VALUES ('delete', old.id, old.final_text);
    INSERT INTO entries_fts(rowid, final_text) VALUES (new.id, new.final_text);
END;

-- 3h. Recreate entity stale-flag trigger (from migration 0004).
CREATE TRIGGER IF NOT EXISTS entries_entity_stale_on_final_text
AFTER UPDATE OF final_text ON entries
BEGIN
    UPDATE entries SET entity_extraction_stale = 1 WHERE id = new.id;
END;

-- ── Step 4: Rebuild entities with user_id ───────────────────────────
-- The UNIQUE constraint changes from (entity_type, canonical_name)
-- to (user_id, entity_type, canonical_name). Each user's "Mom" is
-- a different entity.

CREATE TABLE entities_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    entity_type     TEXT    NOT NULL CHECK(entity_type IN (
                        'person', 'place', 'activity', 'organization', 'topic', 'other'
                    )),
    canonical_name  TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    embedding_json  TEXT,
    first_seen      TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(user_id, entity_type, canonical_name)
);

INSERT INTO entities_new (
    id, user_id, entity_type, canonical_name, description,
    embedding_json, first_seen, created_at, updated_at
)
SELECT
    id, 1, entity_type, canonical_name, description,
    embedding_json, first_seen, created_at, updated_at
FROM entities;

DROP TABLE entities;
ALTER TABLE entities_new RENAME TO entities;

CREATE INDEX IF NOT EXISTS idx_entities_user           ON entities(user_id);
CREATE INDEX IF NOT EXISTS idx_entities_type           ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_canonical_name ON entities(canonical_name);

-- ── Step 5: Add user_id to jobs ─────────────────────────────────────
-- Jobs uses TEXT PK (UUID), so ALTER ADD COLUMN works. SQLite cannot
-- add NOT NULL without DEFAULT on existing rows, so the column is
-- nullable at the schema level; the application layer enforces NOT NULL
-- for new rows. All existing jobs are assigned to the admin user.

ALTER TABLE jobs ADD COLUMN user_id INTEGER REFERENCES users(id);
UPDATE jobs SET user_id = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
