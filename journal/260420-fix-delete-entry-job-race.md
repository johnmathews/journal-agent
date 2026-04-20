# Fix race condition: entry deletion vs running jobs

**Date:** 2026-04-20

## Problem

Deleting an entry via `DELETE /api/entries/{id}` while background jobs (entity extraction, mood scoring)
were still running caused `sqlite3.IntegrityError: FOREIGN KEY constraint failed`. The entity extraction
job tried to INSERT into `entity_mentions` referencing a now-deleted entry. Follow-up jobs also failed
with "Entry not found".

Observed in production on entry 69 — deleted at 14:35:43 while entity extraction job `692dc83f` was
mid-flight (started 14:35:42). The job polled as "running" for ~30 seconds before crashing.

## Fix

Two-layer defence:

1. **API layer (api.py):** `DELETE /api/entries/{id}` now checks for queued/running jobs via
   `has_active_jobs_for_entry()` and returns **409 Conflict** with the active job IDs. The user
   must wait for jobs to finish before deleting.

2. **Extraction layer (entity_extraction.py):** `create_mention()` and `create_relationship()` calls
   catch `sqlite3.IntegrityError` and re-raise as `ValueError("Entry {id} was deleted during extraction")`.
   This produces a clean job failure message if the race still occurs somehow.

## Files changed

- `src/journal/db/jobs_repository.py` — new `has_active_jobs_for_entry()` using `json_extract()`
- `src/journal/api.py` — 409 guard in delete endpoint
- `src/journal/services/entity_extraction.py` — IntegrityError catch in mention/relationship creation
- `docs/api.md` — document the new 409 response
- Tests: 14 new tests across 3 test files
