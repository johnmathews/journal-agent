-- Entity tracking: people, places, activities, topics, etc. extracted
-- from entry text by an on-demand LLM batch job. SQLite is the source
-- of truth; future graph DB implementations will be derived from this
-- schema via the same EntityStore Protocol.

CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type     TEXT    NOT NULL CHECK(entity_type IN (
                        'person', 'place', 'activity', 'organization', 'topic', 'other'
                    )),
    canonical_name  TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    embedding_json  TEXT,           -- JSON-encoded list[float] for dedup stage c; NULL until first set
    first_seen      TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(entity_type, canonical_name)
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_canonical_name ON entities(canonical_name);

-- Alternate surface forms observed in text for a given entity, used
-- for dedup stage b (alias match).
CREATE TABLE IF NOT EXISTS entity_aliases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias_normalised TEXT    NOT NULL,  -- lowercased, stripped
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(entity_id, alias_normalised)
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_normalised ON entity_aliases(alias_normalised);

-- Every mention of an entity in an entry. Multiple mentions of the
-- same entity in the same entry are allowed (e.g. a long entry that
-- refers to a person repeatedly in different contexts).
CREATE TABLE IF NOT EXISTS entity_mentions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id         INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entry_id          INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    quote             TEXT    NOT NULL,  -- verbatim span from the entry text supporting the mention
    confidence        REAL    NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    extraction_run_id TEXT    NOT NULL,  -- UUID per batch run; used to dedupe on rerun
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_mentions_entity ON entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_mentions_entry ON entity_mentions(entry_id);
CREATE INDEX IF NOT EXISTS idx_mentions_run ON entity_mentions(entry_id, extraction_run_id);

-- Relationships between entities, evidenced by a specific entry.
-- predicate is stored as free text (normalisation is a future pass).
CREATE TABLE IF NOT EXISTS entity_relationships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate         TEXT    NOT NULL,
    object_entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    quote             TEXT    NOT NULL,
    entry_id          INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    confidence        REAL    NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    extraction_run_id TEXT    NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_rel_subject ON entity_relationships(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_object ON entity_relationships(object_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_entry ON entity_relationships(entry_id);
CREATE INDEX IF NOT EXISTS idx_rel_run ON entity_relationships(entry_id, extraction_run_id);

-- Stale flag: set to 1 by a trigger when an entry's final_text is
-- updated, so the next batch extraction run knows to reprocess it.
-- The batch extraction service clears the flag when extraction
-- succeeds for the entry.
ALTER TABLE entries ADD COLUMN entity_extraction_stale INTEGER NOT NULL DEFAULT 1;

-- The FTS sync triggers already fire on UPDATE OF final_text, which is
-- the only text-change path in the codebase. Match that pattern here —
-- any change to final_text (via update_final_text in the repository)
-- flags the entry as stale.
CREATE TRIGGER IF NOT EXISTS entries_entity_stale_on_final_text
AFTER UPDATE OF final_text ON entries
BEGIN
    UPDATE entries SET entity_extraction_stale = 1 WHERE id = new.id;
END;
