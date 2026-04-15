# Multi-tenant user_id audit and fixes

## Context

After deploying multi-user auth (260414), testing with the demo user account revealed that
several code paths were not properly scoping data by `user_id`. The bugs manifested as:
"lost connection to server" errors on save, an empty entities page, and batch jobs processing
all users' data.

## Root cause

The multi-tenant migration added `user_id` columns to the database but many service-layer
and API-layer call sites were not threading `user_id` through to the underlying queries. The
pattern was consistent: methods accepted `user_id` as an optional parameter (defaulting to
`None` or `1`), but callers didn't pass it.

## Fixes

### Job polling 404s (most visible bug)
- `submit_reprocess_embeddings`, `submit_entity_extraction`, `submit_mood_score_entry` were
  called without `user_id` in PATCH `/api/entries/{id}`, POST ingest/text, POST ingest/file
- Jobs created with `user_id=NULL` were invisible to non-admin users via `GET /api/jobs/{id}`
- Added `user_id` param to `submit_reprocess_embeddings()` and `submit_mood_score_entry()`
- Passed `user_id` in all 7 affected call sites

### Entity extraction creating entities with wrong user_id
- `extract_from_entry()` → `_resolve_entity()` → `create_entity()` never passed `user_id`
- All entities defaulted to `user_id=1` (admin)
- Threaded `user_id` from entry through entire extraction pipeline: resolve, create, alias
  lookup, relationship resolution
- Also fixed batch extraction: `_resolve_batch_ids()` had no `user_id` filter in its SQL

### Comprehensive audit findings
- Entity mentions endpoint: no user_id filter → joined through entries table
- Entity relationships endpoint: same fix
- Merge candidate PATCH: missing `get_authenticated_user()` entirely
- Merge candidates list: moved from Python post-filter to DB-level JOIN
- Mood backfill: threaded user_id so batch queries only process current user's entries

## Lessons learned

- Multi-tenant filtering must be verified end-to-end after a migration, not just at the
  schema level. Having `user_id` columns means nothing if callers don't use them.
- The production logs were invaluable — they showed jobs succeeding but returning 404 on
  poll, which pointed directly to the permission filter mismatch.
