-- API model pricing configuration.
-- Stores per-model cost information so the frontend can display
-- accurate cost estimates that users can update when providers
-- change their prices.

CREATE TABLE IF NOT EXISTS pricing (
    model TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK(category IN ('llm', 'embedding', 'transcription')),
    input_cost_per_mtok REAL,
    output_cost_per_mtok REAL,
    cost_per_minute REAL,
    last_verified TEXT NOT NULL DEFAULT (date('now'))
);

INSERT OR IGNORE INTO pricing (model, category, input_cost_per_mtok, output_cost_per_mtok, cost_per_minute, last_verified) VALUES
('claude-opus-4-6', 'llm', 5.0, 25.0, NULL, '2026-04-23'),
('claude-sonnet-4-6', 'llm', 3.0, 15.0, NULL, '2026-04-23'),
('claude-sonnet-4-5', 'llm', 3.0, 15.0, NULL, '2026-04-23'),
('claude-haiku-4-5', 'llm', 1.0, 5.0, NULL, '2026-04-23'),
('gemini-2.5-pro', 'llm', 1.25, 10.0, NULL, '2026-04-23'),
('gemini-2.5-flash', 'llm', 0.3, 2.5, NULL, '2026-04-23'),
('gpt-5.4', 'llm', 2.5, 15.0, NULL, '2026-04-23'),
('gpt-4.1', 'llm', 2.0, 8.0, NULL, '2026-04-23'),
('text-embedding-3-large', 'embedding', 0.13, 0, NULL, '2026-04-23'),
('text-embedding-3-small', 'embedding', 0.02, 0, NULL, '2026-04-23'),
('gpt-4o-transcribe', 'transcription', NULL, NULL, 0.006, '2026-04-23'),
('gpt-4o-mini-transcribe', 'transcription', NULL, NULL, 0.003, '2026-04-23');
