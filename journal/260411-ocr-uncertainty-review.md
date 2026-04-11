# 260411 — OCR uncertainty spans & Review toggle (server side)

## What shipped

End-to-end pipeline for OCR uncertainty flagging, from prompt to
database to REST API. The webapp side lands in a matching commit on
`journal-webapp`.

### Migration `0005_uncertain_spans.sql`

New table `entry_uncertain_spans`:

```
id          INTEGER PK
entry_id    INTEGER FK → entries(id) ON DELETE CASCADE
char_start  INTEGER (>= 0)
char_end    INTEGER (> char_start)
created_at  TEXT
```

Indexed on `entry_id`. Spans are half-open `[char_start, char_end)`
offsets into `entries.raw_text`. Never updated — cascades on entry
delete and is otherwise immutable, matching `raw_text` itself.

### OCR provider

- New `OCRResult` dataclass: `text: str` + `uncertain_spans: list[tuple[int, int]]`.
- `OCRProvider` Protocol now defines `extract(...) -> OCRResult` as
  the primary method.
- `AnthropicOCRProvider.extract_text(...)` is kept as a thin wrapper
  returning `.text` for any caller that only wants the string.
- New `parse_uncertain_markers(raw)` helper strips `⟪`/`⟫` sentinels
  and returns `(clean_text, spans)`. Forgiving: unmatched opens /
  closes, nested pairs, and empty/whitespace-only pairs are all
  handled without raising. Inner whitespace is trimmed out of the
  recorded span.
- `SYSTEM_PROMPT` now includes an instruction telling the model to
  wrap uncertain words or phrases in the sentinels, sparingly and
  only around the uncertain span.

### Ingestion service

- Single-page `ingest_image` uses the new `extract` contract and
  calls `repo.add_uncertain_spans(entry.id, result.uncertain_spans)`
  after creating the entry.
- Multi-page `ingest_multi_page_entry` introduces a new helper
  `_strip_and_shift_page_spans` that handles per-page lstrip/rstrip
  and shifts the spans into entry-level coordinates using a running
  `cumulative_offset`. Accounts for the `\n` separator between
  pages. Spans fully inside stripped whitespace are dropped; spans
  that partially overlap are clipped to the kept region.
- Voice transcription is untouched (Whisper doesn't expose an
  uncertainty signal we can use, and it's out of scope for this
  iteration).
- `update_entry_text` (PATCH) is unchanged — `raw_text` is
  immutable, so uncertainty spans persist through edits by
  construction. No special code needed.

### API

- `_entry_to_dict` gains an optional `uncertain_spans` argument and
  always emits the field in the response (empty array when the entry
  has no spans recorded).
- `GET /api/entries/{id}` fetches spans via
  `repo.get_uncertain_spans` and passes them to the serializer.
- `PATCH /api/entries/{id}` does the same, so PATCH responses carry
  the spans the caller expects to see.
- `GET /api/entries` (list) is **not** touched — list responses are
  summary-shaped and don't include `uncertain_spans`. Tested
  explicitly so a future regression would trip the test.

## Key decisions

### Transport: new field on the entry GET, not the tokens API

The original plan was to put `uncertain: bool` on each token
returned by `GET /api/entries/{id}/tokens`. The reconnaissance
showed this would be wrong in two ways:

1. The tokens endpoint tokenizes `final_text` (the corrected text),
   not `raw_text` — mapping uncertainty across the two would
   require a fuzzy alignment that breaks every time the user edits.
2. The webapp's Original OCR panel doesn't render tokens at all —
   it uses `useDiffHighlight` with character-level segments. The
   tokens API serves the Corrected Text panel.

So the feature lives on the entry detail endpoint, anchored to
`raw_text` character offsets, and the webapp will extend its diff
composable to overlay the spans.

### Sentinel characters: `⟪` / `⟫`

Tried three options in my head:

1. Structured JSON response (`{text, uncertain: [...]}`). Rejected
   because it fights the existing prompt-cache setup and adds a
   token-format surface area the model might hallucinate around.
2. ASCII markers (e.g. `[?word?]`). Rejected because they collide
   with legitimate writing — a journal entry can absolutely contain
   brackets and question marks.
3. Rare-Unicode markers. Picked `⟪` (U+27EA) and `⟫` (U+27EB). These
   are math-notation brackets that effectively never appear in
   handwritten English. If they ever do appear in actual journal
   text, the parser silently drops them, which is an accepted tail
   risk.

### Multi-page offset arithmetic

Explicit, tested step-by-step:

1. Parse each page's OCR response → `(text, page_spans)`.
2. Call `_strip_and_shift_page_spans(text, page_spans, cumulative)` —
   returns `(stripped_text, shifted_spans)`.
3. Append `stripped_text` to the combined parts and extend
   `combined_spans` with `shifted_spans`.
4. Bump `cumulative` by `len(stripped_text)`, and by `+1` for the
   `\n` separator between pages (skipped on the last page).
5. Join with `\n` into `entries.raw_text`.
6. `repo.add_uncertain_spans(entry_id, combined_spans)`.

The whitespace clipping step is where the helper earns its keep —
spans that fell entirely in leading/trailing whitespace get
dropped; partial overlaps get clipped to the kept region. Six
multi-page scenarios are covered in
`tests/test_services/test_ingestion.py::TestUncertainSpansIngestion`.

### Glossary interaction: deferred

The glossary priming feature and uncertainty flagging remain
independent for now. They should compose naturally — glossary-primed
words are less likely to be flagged uncertain, and uncertainty
spans give the user an audit surface for "is this really what I
wrote, or did the model pick from the glossary?" A future iteration
might tie them together more tightly (e.g. "flag glossary
substitutions"), but that's not what the user asked for and adding
speculative coupling now would just complicate the tests.

## What didn't change

- **Voice transcription** — out of scope, Whisper doesn't expose an
  uncertainty signal we can use.
- **Tokens API** — deliberately untouched. The Corrected Text panel
  is unaffected by this feature.
- **Re-OCR of old entries** — new ingestions only. Old entries return
  an empty `uncertain_spans` array and the webapp renders the
  Review toggle in a disabled state.

## Tests

- **S1 migration:** 6 new tests in `test_db/test_migrations.py`
  covering version bump, table existence, columns, index, CASCADE,
  and CHECK constraints.
- **S2 repo methods:** 8 new tests in `test_db/test_repository.py`
  covering round-trip, empty case, sort order, empty-list no-op,
  per-entry isolation, cascade delete, and CHECK rejection.
- **S3 OCR parser:** 17 new tests in `test_providers/test_ocr.py`
  covering every docstring-documented behaviour of the parser, plus
  3 tests on the provider itself verifying sentinel stripping and
  the backward-compat `extract_text` wrapper. Plus a regression test
  that fails if `SYSTEM_PROMPT` ever loses its sentinel instruction.
- **S4 ingestion:** 7 new tests in
  `test_services/test_ingestion.py::TestUncertainSpansIngestion`
  covering single-page persistence, multi-page offset shifting,
  leading-whitespace strip/clip, span-fully-inside-trimmed-whitespace
  drop, partial-page uncertainty, and PATCH preservation.
- **S5 API:** 4 new tests in `test_api.py::TestGetEntry` and
  `TestUpdateEntry` covering empty-default, populated spans,
  list-endpoint exclusion, and PATCH preservation.

Full suite: **580 tests pass**, ruff clean.

## Cost impact

One-time cache invalidation when the new `SYSTEM_PROMPT` rolls out
— the next OCR call after deploy pays full price; everything after
that hits the cache again. Negligible.

## Followups (not in scope here)

- Webapp side: `UncertainSpan` type, `useDiffHighlight` extension,
  `Review` toggle in `EntryDetailView`, Playwright visual
  verification. Tracked in the matching commit on `journal-webapp`.
- Eventual graded confidence (low/medium/high) if binary proves
  insufficient in practice.
- Re-OCR flow for old entries if anyone asks for it.
