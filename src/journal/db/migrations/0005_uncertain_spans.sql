-- Per-entry character spans that the OCR model flagged as uncertain.
-- Populated at ingestion time by parsing ⟪ / ⟫ sentinels out of the
-- model's response. Offsets are half-open ranges into `entries.raw_text`
-- (char_start inclusive, char_end exclusive) and are never rewritten
-- after creation — raw_text is immutable, and PATCH /api/entries/:id
-- only touches final_text. Spans cascade when the owning entry is
-- deleted. Entries ingested before this migration have no spans; the
-- webapp renders those with the Review toggle disabled.
--
-- Granularity is binary: a span is uncertain, full stop. A single span
-- may cover a multi-word phrase when the model's doubt applies to the
-- whole phrase.

CREATE TABLE IF NOT EXISTS entry_uncertain_spans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    char_start  INTEGER NOT NULL,
    char_end    INTEGER NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (char_start >= 0),
    CHECK (char_end > char_start)
);

CREATE INDEX IF NOT EXISTS idx_uncertain_spans_entry_id
    ON entry_uncertain_spans(entry_id);
