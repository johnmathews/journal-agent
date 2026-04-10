-- Persist per-entry chunks with source character offsets.
--
-- Before this migration, chunks existed only in ChromaDB as embedded
-- documents with no offset information. That made it impossible to
-- render chunk boundaries over the original text in the webapp.
--
-- This migration adds `entry_chunks`, holding the exact text of every
-- chunk plus the character range it covers in the parent entry's
-- `final_text`. Ingestion writes here from migration 0003 onwards; old
-- entries ingested before this table existed can be populated by
-- running the `backfill_chunk_counts` service (which now writes
-- chunks too) or by re-ingesting.

CREATE TABLE IF NOT EXISTS entry_chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    chunk_text   TEXT    NOT NULL,
    char_start   INTEGER NOT NULL,
    char_end     INTEGER NOT NULL,
    token_count  INTEGER NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(entry_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_entry_chunks_entry ON entry_chunks(entry_id);
