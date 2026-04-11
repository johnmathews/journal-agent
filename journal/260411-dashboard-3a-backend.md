# 2026-04-11 — Dashboard 3a backend (Tier 1 item 3a)

Session 4 of the Tier 1 plan. Backend half of sub-epic 3a from
`docs/tier-1-plan.md`. Closes work units **T1.3a.i** and
**T1.3a.ii**. The matching webapp work (`T1.3a.iii`–`.vi`,
`/dashboard` route, `DashboardView.vue`, charts) ships as a
sibling commit in `journal-webapp`.

## What shipped

### 1. `WritingFrequencyBin` model

New dataclass in `src/journal/models.py`:

```python
@dataclass
class WritingFrequencyBin:
    bin_start: str      # ISO-8601 date, canonical bucket start
    entry_count: int
    total_words: int
```

`bin_start` is always the first day of the bucket in canonical
form: Monday for weeks, 1st of the month for months, 1st of
Jan/Apr/Jul/Oct for quarters, January 1 for years. Frontend
charts plot any metric against `bin_start` as the x-axis.

### 2. `get_writing_frequency` repository method

New method on `SQLiteEntryRepository`:

```python
def get_writing_frequency(
    start_date: str | None,
    end_date: str | None,
    granularity: str,
) -> list[WritingFrequencyBin]
```

`granularity` is validated against a hardcoded `_SUPPORTED_BINS`
tuple (`"week"`, `"month"`, `"quarter"`, `"year"`). Unsupported
values raise `ValueError` before any SQL runs so the endpoint
can surface a clean 400.

Each granularity computes `bin_start` in SQL using date
arithmetic so the result is already the canonical bucket-start
date, not a `%Y-W%W`-style string the frontend would have to
parse:

- **week:** `date(entry_date, '-N days')` where `N =
  (strftime('%w') + 6) % 7`. SQLite's `%w` returns 0 for Sunday
  .. 6 for Saturday, so a Sunday (`%w=0`) rolls back `(0+6) % 7
  = 6` days to the preceding Monday, a Tuesday (`%w=2`) rolls
  back `(2+6) % 7 = 1` day, etc.
- **month:** `date(entry_date, 'start of month')`.
- **quarter:** `date(entry_date, 'start of month', '-M months')`
  where `M = (strftime('%m') - 1) % 3`. Start-of-month first,
  then subtract 0/1/2 months to land on the quarter start.
- **year:** `date(entry_date, 'start of year')`.

Results are grouped by `bin_start`, sorted ascending, and
**empty buckets are omitted**. A month with zero entries does
not appear in the list — clients rendering a dense line chart
are expected to fill gaps themselves. This keeps the backend
response size proportional to actual activity and avoids baking
a "fill missing bins" policy into the repository.

Tests: 10 unit tests in `test_repository.py::TestWritingFrequency`
covering the invalid-granularity error, empty corpus, each of the
four granularities, multi-entry bucket aggregation, date filter
clamping, explicit assertion that empty buckets are omitted, and
sort order.

### 3. `GET /api/dashboard/writing-stats` endpoint

New route in `api.py`:

```
GET /api/dashboard/writing-stats?bin=week&from=&to=
```

Combined endpoint returning both `entry_count` and `total_words`
per bucket (per the plan's open question #5 decision — one
endpoint, two metrics, matches the underlying SQL and saves the
frontend a second round trip). Response envelope:

```json
{
  "from": "2026-01-01",
  "to": "2026-04-11",
  "bin": "month",
  "bins": [
    {"bin_start": "...", "entry_count": N, "total_words": N}
  ]
}
```

Validation lives at the repo boundary (`ValueError` → 400
`invalid_bin`). Bearer auth is inherited from
`BearerTokenMiddleware`. 503 when services are not initialised.

Tests: 8 integration tests in `test_api.py::TestDashboardWritingStats`
covering default bin (week), each explicit granularity, invalid
bin → 400, date filter, empty corpus → empty list, 503 path.

## Decisions made during this session

1. **Backend validates granularity, not the route handler.** The
   route passes `bin_param` straight through to the repo and
   catches `ValueError`. This keeps the list of supported values
   in one place (`_SUPPORTED_BINS` in `repository.py`) and the
   route doesn't need its own tuple to stay in sync.
2. **Quarter bins computed from month modulo.** SQLite has no
   `%Q` strftime format. A modulo-based computation in the SQL
   expression is clearer than introducing a CASE statement and
   performs identically on modern SQLite.
3. **Empty buckets omitted, not zero-filled.** The repo returns
   only non-empty bins. Dense-series rendering is a presentation
   concern that belongs on the client, where the chosen chart
   library already has opinions about how to handle gaps.
4. **No `total_chunks` in the response yet.** The `entries`
   table already has `chunk_count`, so adding a third metric to
   each bin would be free, but nothing in 3a actually needs it.
   Saved for later — the endpoint can grow by adding fields
   without breaking clients that ignore them.

## Tests and quality gates

- **Before:** 436 tests passing.
- **After:** 454 tests passing. +18 new tests:
  1. `TestWritingFrequency` — 10 unit tests
  2. `TestDashboardWritingStats` — 8 integration tests
- **Ruff:** clean on `src/` and `tests/`.
- Coverage target (65%) held.

## Follow-ups

1. **Webapp sibling commit.** The frontend half of 3a (work
   units T1.3a.iii–vi: DashboardView, Pinia store, Chart.js
   integration, Option B routing, sidebar default-expanded fix,
   Playwright verification) ships in `journal-webapp` right
   after this commit lands.
2. **Dashboard 3b (mood scoring)** waits for its own session.
   Introduces a new Haiku LLM call in the ingestion path, a
   `JOURNAL_ENABLE_MOOD_SCORING` config flag, and a
   `journal backfill-mood` CLI. Deliberately not bundled into
   this session — the LLM integration has a different risk
   profile than pure SQL aggregation.
3. **Dashboard 3c (people + topic charts)** remains blocked on
   Item 1 (first real entity extraction run against actual
   corpus data).
