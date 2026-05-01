# 2026-05-01 — Dedicated Pushover toggles for save-entry pipeline

## What

Two new notification topics in `services/notifications.py`:

- `notif_job_success_save_entry` — "Entry update succeeded" (success group, default True)
- `notif_job_failed_save_entry` — "Entry update failed" (failure group, default True)

These give the user direct control over the consolidated Pushover notification
that fires after editing and saving a journal entry — the one that summarises
the post-save pipeline (entity extraction + mood analysis + embedding
reprocessing).

`save_entry_pipeline` is now mapped through:

- `_SUCCESS_TOPIC_MAP["save_entry_pipeline"]` → `notif_job_success_save_entry`
- New `_PIPELINE_FAILURE_TOPIC_MAP["save_entry_pipeline"]` → `notif_job_failed_save_entry`

`notify_pipeline_failed` now consults `_PIPELINE_FAILURE_TOPIC_MAP` first and
falls back to the global `notif_job_failed` for any other parent job type.

## Why

Save-entry pipeline notifications are the noisiest in normal use — every edit
triggers a Pushover. The user wanted a dedicated toggle on the `/settings` page
for both success and failure paths, separate from the global "Job failed
permanently" and ingestion-success toggles.

Previously the pipeline-failure path used the global `notif_job_failed` topic,
which meant silencing save-entry failures forced the user to silence all job
failures. Success had no toggle at all — `notify_job_success("save_entry_pipeline", ...)`
fell into the "no topic key → always notify" branch.

## How

Server only — no DB migration needed because preferences are stored as
JSON key-value rows in `user_preferences` (one row per `(user_id, key)`), so
new keys just work via the existing `set_preference()` path.

The webapp's `/settings` view reads topic definitions live from
`GET /api/notifications/topics`, groups them by `group`, and renders one
toggle per topic — so the two new toggles appear automatically next time the
page loads. No webapp code change needed.

## Tests

`tests/test_services/test_notifications.py`:

- `test_save_entry_pipeline_success_gated_by_dedicated_topic` — toggling
  `notif_job_success_save_entry=False` silences save-entry success.
- `test_save_entry_pipeline_success_fires_when_enabled` — default-true
  behaviour.
- `test_save_entry_pipeline_success_independent_of_ingest_topics` — silencing
  image-ingestion success does NOT silence save-entry success (and vice versa,
  by symmetry).
- `test_skips_when_save_entry_failure_topic_disabled` — flipped from the old
  test that gated on the global `notif_job_failed`. Now uses the dedicated key.
- `test_save_entry_failure_not_gated_by_global_failed_topic` — silencing the
  global `notif_job_failed` does NOT silence save-entry pipeline failures.
- `test_non_admin_sees_no_admin_topics` — bumped expected user-visible topic
  count from 4 → 6 (3 success + 3 failure).

## Out of scope

- No DB migration (preferences are schemaless KV).
- No webapp UI change — toggles render automatically.
- The retrying-backoff topic (`notif_job_retrying`) is still global; not split
  per pipeline. Retries are rare enough that a single toggle is fine.
