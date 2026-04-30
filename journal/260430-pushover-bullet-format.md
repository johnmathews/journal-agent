# 2026-04-30 — Unified bullet format for Pushover notifications

All Pushover notification bodies now use the same verb-first bullet
format so the user can scan results at a glance.

## What changed

Every notification body produced by
`journal.services.notifications` now uses `- `-prefixed lines (or
`✓ ` / `✗ ` in the partial-failure path). Each bullet leads with a
verb where possible.

Updated paths:

- `_build_success_message` — covers `ingest_images` / `ingest_audio`,
  `entity_extraction`, `mood_backfill`, `mood_score_entry`,
  `reprocess_embeddings`, `save_entry_pipeline`, and the generic
  fallback.
- `build_pipeline_failure_body` — successful stages prefixed `✓ `,
  failed stages prefixed `✗ `. Replaces the previous `+ ` / `- `
  convention.
- `notify_job_retrying`, `notify_job_failed`,
  `notify_admin_job_failed` — bodies broken into bullets
  (`- Cause: …`, `- Error: …`, `- Retrying in N min (attempt N)`).
- `notify_health_alert` — detail wrapped as a single bullet.
- `send_test_notification` — bullet-formatted confirmation.

## Why

The previous format was inconsistent (some prose, some labelled
lines, some comma-joined counts) and hard to skim on a phone lock
screen. The new format is one shape everywhere: title on top,
bullets below, verbs first.

## Constant counts dropped

`mood_score_entry` and the per-entry mood result inside the
ingestion pipelines previously reported `"7 mood scores"`. The
count is structurally constant — one score per mood dimension —
so the number adds nothing. Both paths now show only
`- Calculated mood scores`.

## Why ✓ / ✗ for partial failures

For `build_pipeline_failure_body`, the per-stage outcome
(succeeded vs failed) is information the user needs at a glance.
Plain `- ` bullets would lose that distinction. Unicode marks
preserve the bullet-list shape while keeping the success/failure
signal scannable.

## Tests

Existing assertions in `test_notifications.py` were updated for the
new format. Two new tests were added (`mood_score_entry`,
`reprocess_embeddings`) since those branches were previously
uncovered. Loose substring assertions in `test_jobs_runner.py`
(e.g. `"Reprocessed" in body`) continue to pass without change.

Full suite: 1407 passed.
