# Live reload for file-backed config

## What shipped

Three admin-only endpoints that re-read the file-backed config without
restarting the server:

- `POST /api/admin/reload/ocr-context` — rebuilds the OCR provider with
  the current `OCR_CONTEXT_DIR/*.md` glossary.
- `POST /api/admin/reload/transcription-context` — rebuilds the
  transcription provider stack (Whisper + optional Retrying / Shadow
  wrappers, or Gemini) with the current context.
- `POST /api/admin/reload/mood-dimensions` — re-reads
  `MOOD_DIMENSIONS_PATH` and rebuilds the `MoodScoringService`. Returns
  409 if `JOURNAL_ENABLE_MOOD_SCORING` is unset.

The swap logic lives in `services/reload.py` and is also called from
the runtime-settings hook in `mcp_server.py` — both code paths now
share one implementation, so a future fix to provider construction
lands everywhere it needs to.

## Why this scope

- **No filesystem watcher.** Operator-triggered is enough — context
  edits are a deliberate, low-frequency act. A watcher would add a
  daemon thread and a debounce layer for very little gain.
- **No automatic reload on edit.** Same reason.
- **OCR and transcription deliberately not coupled.** Both helpers
  consume `OCR_CONTEXT_DIR`, but the formatter chains differ (Whisper
  prompt builder vs Anthropic system text vs Gemini, plus Retrying /
  Shadow wrappers on the transcription side). A shared reload would
  hide that divergence the moment it appears. Two curls is the right
  tax. The webapp will surface both as separate buttons.
- **No `CACHEABLE_MINIMUM_TOKENS` warning suppression.** The Anthropic
  OCR provider's constructor logs a warning when context tokens are
  below the minimum useful for prompt caching. After a reload, that
  warning will fire again — that's correct behaviour, not noise.

## Concurrency

Python attribute writes are atomic. The helpers do
`services["ingestion"]._ocr = new_ocr` etc.; an in-flight request that
already resolved `_ocr` keeps its reference and finishes against the
old provider. The next request reads the freshly-bound attribute and
gets the new one. No locks, no special teardown — the old provider is
garbage-collected once the last in-flight call releases it. Tests
exercise this directly (`test_picks_up_new_context_files` holds the
old reference, edits the file, reloads, and verifies the old reference
still reflects the pre-edit state).

## Interaction with the runtime-settings hook

`mcp_server.py:_on_runtime_setting_change` already had ad-hoc swap
logic for OCR (when `ocr_provider` / `ocr_dual_pass` change) and for
mood scoring (when `enable_mood_scoring` is flipped on). Both branches
are now thin wrappers around the new helpers — they build a patched
`Config` with the runtime override applied and call
`reload_ocr_provider` / `reload_mood_dimensions`. Behaviour is
identical; the existing runtime-settings tests pass unchanged. The
"flip mood scoring off" branch still inlines its own None-write
because that's not a reload, it's a teardown.

## Tests

- `tests/test_services/test_reload.py` — 11 tests covering swap
  semantics, picks-up-new-content, in-flight reference survival,
  summary shape, and the disabled-mood error path.
- `tests/test_auth_api.py::TestAdminReload*` — 10 tests covering
  401 (no token), 403 (non-admin), 200 (admin + summary shape),
  and 409 (mood reload while disabled).

## Webapp follow-up

The companion webapp PR adds `AdminServerView.vue` at `/admin/server`
with three reload buttons gated on `is_admin`. See
`journal-webapp/journal/260501-admin-server-reload.md`.
