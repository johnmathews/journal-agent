# 2026-04-11 — Search backend (Tier 1 item 4)

Session 1 of the Tier 1 plan: ship the backend half of the Search UI
so a later frontend session can bolt on a `/search` view without any
more server work. This closes work units **T1.4.a** (keyword search
service wrapper), **T1.4.b** (chunk offsets on `ChunkMatch`), and
**T1.4.c** (`GET /api/search` endpoint) from `docs/tier-1-plan.md`.

## What shipped

### 1. Model additions (`src/journal/models.py`)

`ChunkMatch` gained three optional fields, all defaulting to `None`
so existing construction sites keep working unchanged:

- `chunk_index: int | None` — the chunk's position in its parent
  entry's chunk list, as stored in `entry_chunks.chunk_index` and
  in ChromaDB metadata under the `chunk_index` key.
- `char_start: int | None` — offset into the parent entry's
  `final_text` (or `raw_text` fallback), matching
  `entry_chunks.char_start`.
- `char_end: int | None` — exclusive end offset, ditto.

All three are `None` for entries ingested before migration 0003
(chunk persistence), because there are no `entry_chunks` rows to
JOIN against. Docstrings spell this out so overlay consumers know
to guard against missing offsets.

`SearchResult` gained one optional field:

- `snippet: str | None` — populated only in keyword search mode.
  It's a substring of `final_text` with ASCII `\x02` (STX) and
  `\x03` (ETX) control characters wrapping matched terms. These
  marker characters don't appear in normal journal text and survive
  JSON serialisation intact (JSON escapes them as `\u0002`/`\u0003`).
  The frontend replaces them with whatever highlight markup it
  wants.

### 2. Repository (`src/journal/db/repository.py`)

Two new methods, both on the `EntryRepository` Protocol and the
SQLite implementation:

- `search_text_with_snippets(query, start_date, end_date, limit,
  offset) -> list[tuple[Entry, str]]` — FTS5 keyword search using
  the `snippet(entries_fts, 0, char(2), char(3), '…', 16)` aux
  function. Column index `0` is the single indexed column
  `final_text` (confirmed by reading migration 0002). The literal
  `'…'` (U+2026) is the FTS5 ellipsis marker for truncated context.
  Ordered by FTS5's `rank`, paginated with `LIMIT`/`OFFSET`.
- `count_text_matches(query, start_date, end_date) -> int` — a
  lightweight count variant of the same query. Not used by the
  current endpoint (it doesn't return a total) but added because
  the plan hinted at it and it's two cheap SQL lines.

The existing `search_text()` method is intentionally left untouched
so `get_topic_frequency` (its only other caller) keeps working.

### 3. Query service (`src/journal/services/query.py`)

`search_entries()` now enriches each `ChunkMatch` with char
offsets. After grouping chunks by entry, it calls
`repository.get_chunks(entry_id)` once per matched entry and uses
the `chunk_index` from Chroma metadata to look up the corresponding
`entry_chunks` row. Entries without persisted chunks skip the
enrichment step — the `ChunkMatch` is still returned, just with
`char_start`/`char_end` left as `None`. `chunk_index` is always
populated from Chroma metadata.

New method `keyword_search(query, start_date, end_date, limit,
offset) -> list[SearchResult]` delegates to the new repository
method and wraps each `(Entry, snippet)` tuple in a `SearchResult`
with `matching_chunks=[]` and `snippet=<fts5 snippet>`. The
per-result `score` is a linear decay from 1.0 (best hit) across the
page so clients that re-sort by `score` preserve FTS5's rank
ordering. The score is deliberately not comparable to semantic
mode scores — it's only a stable-ordering hint.

### 4. REST endpoint (`src/journal/api.py`)

`GET /api/search` with params:

- `q` — required, trimmed, 400 with `error: missing_query` if empty.
- `mode` — `semantic` (default) or `keyword`. 400 with
  `error: invalid_mode` for anything else.
- `start_date`, `end_date` — ISO 8601 date filters.
- `limit` — default 10, clamped to `[1, 50]`, falls back to 10 on
  non-integer.
- `offset` — default 0, clamped non-negative, falls back to 0 on
  non-integer.

The endpoint also catches `sqlite3.OperationalError` from FTS5
parse failures (unterminated quote, bare operator, etc.) and
returns a `400 invalid_query` with the FTS5 error message instead
of leaking a 500. Added during code review after verifying that
`SELECT * FROM entries_fts WHERE entries_fts MATCH '"'` raises
`OperationalError: unterminated string`.

Response envelope: `{query, mode, limit, offset, items:
[SearchResult...]}`. Each item has `entry_id`, `entry_date`,
`text` (full parent entry), `score`, `snippet`, and
`matching_chunks` (list of `{text, score, chunk_index, char_start,
char_end}`).

Bearer auth is inherited from `BearerTokenMiddleware` which wraps
every route on the Starlette app built by FastMCP in `main()`, so
no explicit auth wiring is needed here.

## Open questions answered during this session

From the plan's "Open questions (need decisions before coding)"
list, Session 1 needed answers to #7 and #8. Both took the plan's
recommended defaults:

- **#7 — Search mode default:** `semantic`, matching how the user
  would use the tool day-to-day.
- **#8 — Snippet generator for keyword mode:** yes, FTS5
  `snippet()` with `\x02`/`\x03` markers. Tokens chosen so the
  API response is independent of frontend markup choice.

## Tests

- **Before:** 368 tests passing.
- **After:** 392 tests passing. +24 new tests:
  1. `tests/test_db/test_repository.py::TestFTSSnippets` — 6 tests
     covering snippet marker wrapping, date filter, pagination,
     no-match, and the `count_text_matches` helper.
  2. `tests/test_services/test_query.py::TestSearchEntriesChunkOffsets`
     — 2 tests covering offset enrichment and graceful `None`
     offsets for legacy entries.
  3. `tests/test_services/test_query.py::TestKeywordSearch` — 5
     tests covering snippet return, date filter, pagination,
     no-match, and stable score ordering.
  4. `tests/test_api.py::TestSearch` — 11 tests covering missing
     query, empty query, invalid mode, keyword happy path, keyword
     date filter, keyword pagination, keyword no-match, malformed
     FTS5 query (400), limit clamping, semantic chunk offsets, and
     default mode inference.

Coverage on the touched modules after the changes:

- `src/journal/db/repository.py`: 93%
- `src/journal/services/query.py`: 89%
- `src/journal/api.py`: 57% (the uncovered lines are the entity
  routes, which are tested in `test_entity_api`-style files
  excluded from this subset run).

`uv run ruff check src/ tests/` is clean.

## Deliberate non-goals for this session

Kept out of scope so the session stayed bounded:

1. No frontend work. Session 2 of the plan wires the webapp
   `/search` view, `SearchView.vue`, store, and highlight
   rendering. That work happens in the `journal-webapp` repo, not
   here, and needs its own worktree.
2. No MCP tool changes. `journal_search_entries` (the LLM-facing
   tool) already renders `matching_chunks` as text snippets and
   ignores char offsets — my model changes are additive-only so
   the tool output is unchanged. A future session could surface a
   `journal_keyword_search` tool for explicit keyword-mode
   consumption, but that's speculative until the LLM side actually
   wants it.
3. No `total` field on the response envelope. Semantic mode can't
   return a cheap total (would require an extra over-fetch against
   Chroma). Keyword mode can (`count_text_matches` is ready), but
   mixing would make the response shape inconsistent between modes.
   For now clients infer "more available" from
   `len(items) == limit`. Revisit if a real use case appears.

## Follow-ups

1. **Session 2 — frontend `/search` view** (next session). Needs
   to consume this endpoint, render the snippet with a client-side
   `\x02`/`\x03` → `<mark>` transform for keyword mode, and re-use
   `useOverlayHighlight` or a variant for semantic mode chunk
   highlights. Click-through to `EntryDetailView` with a query
   param like `?chunk=N` to scroll the top matching chunk into
   view.
2. **Risk #3 from the plan** — verify that legacy multipage
   entries (with the old `"\n\n"` page-join) render highlights
   correctly from the existing `entry_chunks.char_start`/`char_end`.
   Their stored offsets were computed against the old-separator
   text, so as long as `final_text` still contains that original
   text the highlights will align. This is a manual check once
   the frontend lands.
