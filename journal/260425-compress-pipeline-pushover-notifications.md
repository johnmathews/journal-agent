# Compress pipeline Pushover notifications into a single message

## Problem

Ingestion pipelines (audio and image) sent 3 separate Pushover notifications
on the happy path: one when the parent ingestion job completed, one when mood
scoring completed, and one when entity extraction completed. The design
intention (documented in 260423-pushover-notifications.md) was "1 notification
for the happy path, not 4 per pipeline stage", but the implementation didn't
deliver on that — each job independently called `_notify_success`.

The in-browser toast compression (webapp commit 5ced1d3) was working correctly;
this was a server-side issue only.

## Solution

Follow-up jobs (mood scoring, entity extraction) now carry a `parent_job_id`
in their params when auto-triggered by an ingestion pipeline. This lets each
follow-up distinguish between "I was triggered as part of a pipeline" vs
"I was manually triggered as a standalone batch job".

### Notification flow

1. Parent ingestion job completes → **no notification** (suppressed)
2. Each follow-up completes → calls `_try_pipeline_notification(parent_job_id)`
3. `_try_pipeline_notification` checks if all sibling follow-ups have succeeded
4. If not all done yet → no-op (wait for the last one)
5. If all succeeded → merge results from parent + all follow-ups into one dict,
   send a single combined Pushover notification

### Combined notification content

The single notification includes:
- Entry ID (from parent ingestion result)
- Mood score count (from mood scoring result)
- Entity + mention counts (from entity extraction result)

Example: "Entry 76 created / 7 mood scores / 8 entities, 18 mentions"

### Standalone batch jobs unaffected

Jobs triggered manually (entity extraction batch, mood backfill) have no
`parent_job_id` in their params, so they continue to notify individually.

## Files changed

- `src/journal/services/jobs.py` — Added `parent_job_id` to allowed params for
  entity_extraction and mood_score_entry; modified `_queue_post_ingestion_jobs`
  to pass it; suppressed parent ingestion notification; added
  `_try_pipeline_notification` helper; wired follow-up runners to use it
- `src/journal/services/notifications.py` — Updated `_build_success_message` to
  include mood + entity results in ingestion messages when available
- `tests/test_services/test_jobs_runner.py` — 5 new tests: audio pipeline sends
  one notification, image pipeline sends one notification, standalone entity
  extraction still notifies, standalone mood scoring still notifies,
  parent_job_id stored in follow-up params
- `tests/test_services/test_notifications.py` — 1 new test: combined message
  includes mood + entity results

## Why server-side, not webapp

The webapp toast compression (commit 5ced1d3) groups related frontend toasts.
But Pushover notifications are sent by the server — the webapp has no control
over them. The server needed its own pipeline grouping logic.
