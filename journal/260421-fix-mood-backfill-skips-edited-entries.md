# Fix mood backfill skipping edited entries

**Date:** 2026-04-21

## Problem

Mood backfill (`--stale-only` mode) completed in ~5 ms with 0 scored / 0 skipped after
an entry was edited via OCR correction. The backfill was triggered but immediately
determined there was nothing to do.

## Root cause

`get_entries_missing_mood_scores` only checked whether all configured dimension rows
existed for an entry. It never compared `entries.updated_at` against
`mood_scores.created_at`. So an entry that was fully scored and then had its text
corrected still appeared "complete" — the backfill skipped it.

## Fix

Added an `OR` condition to the staleness SQL query in `repository.py`:

```sql
OR e.updated_at > (
    SELECT MAX(m2.created_at)
    FROM mood_scores m2
    WHERE m2.entry_id = e.id
)
```

An entry is now considered stale when either:
1. It is missing at least one current dimension, **or**
2. Its `updated_at` is newer than the latest `mood_scores.created_at`

The `MAX` subquery returns `NULL` for entries with no scores at all, and
`updated_at > NULL` evaluates to `NULL` (falsy), so condition 1 still covers
the "never scored" case correctly.

## Tests added

- `test_get_entries_missing_mood_scores_detects_edited_text` — repo-level unit test
- `test_stale_only_rescores_edited_entries` — end-to-end backfill regression test

Both use a 1.1 s sleep to ensure SQLite's 1-second `strftime` resolution produces
distinct timestamps for the initial score and the subsequent edit.

## Files changed

- `src/journal/db/repository.py` — `get_entries_missing_mood_scores` SQL + docstring
- `src/journal/services/backfill.py` — `backfill_mood_scores` docstring
- `docs/mood-scoring.md` — updated `--stale-only` description
- `tests/test_db/test_repository.py` — new test
- `tests/test_services/test_backfill.py` — new regression test
