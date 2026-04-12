-- Merge history: records every merge so it can be reviewed or undone.
-- When entities A and B are merged into survivor S, one row is created
-- per absorbed entity. The snapshot columns capture enough state to
-- reconstruct the absorbed entity on split.

CREATE TABLE IF NOT EXISTS entity_merge_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    survivor_id      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    absorbed_id      INTEGER NOT NULL,  -- no FK: the row is deleted after merge
    absorbed_name    TEXT    NOT NULL,   -- canonical_name at time of merge
    absorbed_type    TEXT    NOT NULL,   -- entity_type at time of merge
    absorbed_desc    TEXT    NOT NULL DEFAULT '',
    absorbed_aliases TEXT    NOT NULL DEFAULT '[]',  -- JSON list of alias strings
    merged_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    merged_by        TEXT    NOT NULL DEFAULT 'user'  -- 'user' or 'auto' (stage-c)
);

CREATE INDEX IF NOT EXISTS idx_merge_history_survivor ON entity_merge_history(survivor_id);

-- Merge candidates: persists stage-c warnings from extraction so the
-- merge review UI can surface them across sessions.

CREATE TABLE IF NOT EXISTS entity_merge_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id_a     INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_id_b     INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    similarity      REAL    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'dismissed')),
    extraction_run_id TEXT  NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    resolved_at     TEXT,
    UNIQUE(entity_id_a, entity_id_b, extraction_run_id)
);

CREATE INDEX IF NOT EXISTS idx_merge_candidates_status ON entity_merge_candidates(status);
