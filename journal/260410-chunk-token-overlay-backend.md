# 2026-04-10 — Chunk & token overlay backend

Added the server-side pieces of the webapp chunk/token overlay feature. Companion commit lands on the webapp side on the same branch.

## What changed

**New `ChunkSpan` dataclass in `models.py`** — chunks now carry their source character range and token count in addition to the rendered text.

**`ChunkingStrategy.chunk()` return type is now `list[ChunkSpan]`** (was `list[str]`). Both `FixedTokenChunker` and `SemanticChunker` were refactored to track source offsets internally:

- `_split_paragraphs_with_offsets()` mirrors `text.split("\n\n")` but returns `_Piece` records with `start`/`end` positions. Offsets are computed arithmetically from the paragraph separator width — fast and deterministic.
- `_split_sentences_with_offsets()` uses pysbd as before but resolves each stripped sentence's position in the source by scanning forward with `str.find()`. pysbd normalises whitespace inside sentences, so arithmetic alone doesn't work here. Sentences that can't be located log a warning and fall back to the cursor position (rare — only for pathological inputs).
- `_split_long_paragraph()` now takes a `_Piece` (not a bare string) and shifts sentence offsets by the paragraph's own `start` so the resulting `ChunkSpan`s carry offsets into the *source* text, not into the paragraph.

**Migration `0003_entry_chunks.sql`** adds the `entry_chunks` table: `(entry_id, chunk_index, chunk_text, char_start, char_end, token_count)` with a unique constraint on `(entry_id, chunk_index)` and an index on `entry_id`. FK cascade removes chunk rows when the parent entry is deleted.

**Repository extensions** on `SQLiteEntryRepository`:
- `replace_chunks(entry_id, chunks)` — transactional delete-then-insert, so re-ingest and rechunk paths get correct behaviour for free.
- `get_chunks(entry_id)` — returns chunks ordered by `chunk_index`.

**Ingestion persists chunks now.** `IngestionService._process_text` calls `repository.replace_chunks(entry_id, chunks)` before computing embeddings. Deliberate ordering: if embeddings fail, we still have accurate SQLite state for the overlay.

**Backfill extended.** `backfill_chunk_counts` now writes to `entry_chunks` as well as updating `chunk_count`. Legacy entries (pre-0003) can be populated by running the existing backfill service — no re-ingestion required. "Unchanged" now means both the count matches AND the `entry_chunks` row count matches, otherwise the row set is rewritten.

**Two new API endpoints:**

- `GET /api/entries/{id}/chunks` — returns persisted chunks with offsets. Distinguishes `entry_not_found` from `chunks_not_backfilled` via the `error` field so the webapp can surface a clear message.
- `GET /api/entries/{id}/tokens` — on-demand tiktoken `cl100k_base` tokenisation using `encode()` + `decode_with_offsets()`. Returns per-token `{index, token_id, text, char_start, char_end}`. The encoding object is cached at module load. For valid UTF-8 input the offsets slice `final_text` exactly, which is what the overlay needs.

## Why on-demand for tokens

Considered precomputing tokens at ingest time and storing them in SQLite. Rejected:
1. Token overlay is cheap — `cl100k_base` runs at ~1 MB/s on modern hardware. A 2000-word entry tokenises in < 10 ms. Not worth caching.
2. Precomputing adds a cache invalidation axis: every time `final_text` is edited via PATCH, the token rows would need to be rewritten. That's more state to keep correct with no user-visible benefit.
3. Tokens are a pure function of text. Compute when needed.

Chunks are different — they depend on the chunker's config (`max_tokens`, strategy, etc.) which can change without the text changing. Persisting them means the overlay reflects the state at ingest, which is also what retrieval sees. That's the desired semantics.

## Why not Claude tokens

Original plan mentioned a toggle between OpenAI and Anthropic tokenisers. Dropped during research:

- Anthropic does not expose per-token boundaries for Claude 3+/4. `client.messages.count_tokens()` returns `{"input_tokens": <int>}` — just a count.
- Community tokenisers exist but their accuracy isn't endorsed by Anthropic.
- OpenAI `cl100k_base` is the tokeniser that actually drives the chunker and retrieval, so it's the one that matters for understanding how journal text is being indexed.

Decision captured in `.engineering-team/findings-overlay-feature.md`.

## The 277→5 chunks mystery

The overlay was built in part as a diagnostic tool for this. Research already suggested the cause:

- `FixedTokenChunker` default `max_tokens=150`. A 277-word entry is roughly 330–370 tokens in `cl100k_base`. That guarantees at least 3 chunks.
- Multi-page OCR entries are joined with `"\n\n".join(page_texts)` in `ingestion.py:272`. Each OCR page boundary becomes a paragraph split, which can force additional chunk breaks before the token budget is exhausted.

Not fixing this here — the point was to make it visible first. Once the webapp overlay lands, this should be immediately inspectable in the browser.

## Testing

272 server tests pass. New tests added:

- `test_chunking.py`: offset invariants for `FixedTokenChunker` and `SemanticChunker` (slice round-trip, leading-whitespace handling, sentence containment).
- `test_db/test_migrations.py`: table exists, columns correct, FK cascade works.
- `test_db/test_repository.py`: `replace_chunks` and `get_chunks` covering insert, replace, clear, order, cascade delete.
- `test_services/test_ingestion.py`: `TestChunkPersistence` covering image/voice ingest, update path (replaces chunks), delete (cascades).
- `test_services/test_backfill.py`: `test_populates_entry_chunks_table` (legacy entries), `test_second_run_leaves_chunk_rows_unchanged` (idempotency).
- `test_api.py`: `TestGetEntryChunks` (happy path, `chunks_not_backfilled`, `entry_not_found`) and `TestGetEntryTokens` (reconstruction, unicode, `final_text` preference, 404).

Coverage: 76% (up from baseline — new code is heavily tested, chunker helpers cover most of chunking.py's additions). ruff clean.

## Follow-ups

1. Run `backfill_chunk_counts` against the production DB once the webapp side ships, so legacy entries get their `entry_chunks` rows populated before users try to use the overlay on them.
2. Investigate the 277→5 behaviour using the overlay once it's rendering in the browser. Likely tweaks will be to `FixedTokenChunker`'s defaults, the page-join separator, or both.
3. The `SemanticChunker` offset tracking uses `str.find()` with forward-only cursor. If pysbd ever starts normalising punctuation (currently only whitespace), offsets could drift — the warning log would surface it.
