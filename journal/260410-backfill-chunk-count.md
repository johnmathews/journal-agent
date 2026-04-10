# Backfill chunk_count for entries with stale counts

**Date:** 2026-04-10
**Sibling commit:** journal-webapp — `260410-live-diff-editor.md`

## Problem

The webapp homepage showed `CHUNKS = 0` for every entry, even entries that had been ingested successfully and had non-zero word counts. Root cause, after tracing through the code and schema:

1. Migration `0002_multi_page_and_correction.sql` added the `chunk_count` column to `entries` with `DEFAULT 0`. Any row that existed before that migration ran was backfilled to 0 and never updated.
2. The `seed` CLI command (`src/journal/cli.py:cmd_seed`) inserted entries directly via `repo.create_entry()` without ever running the chunker. So any database populated via `journal seed` had stale counts regardless of migration history.
3. There was already a `backfill-chunks` command, but it only counted documents in ChromaDB for each entry. For seeded or pre-migration entries, there were no ChromaDB docs to count — the command was a no-op.

## Fix

Two independent changes:

1. **`seed` now computes `chunk_count` locally.** `cmd_seed` calls `chunk_text(...)` with the sample text and writes the result via `repo.update_chunk_count()`. No embeddings are generated — that still requires API keys — but the UI column is now correct.

2. **`backfill-chunks` now re-runs the chunker over every entry.** The command logic moved out of `cli.py` into a new `services/backfill.py::backfill_chunk_counts` function that:
   - Iterates all entries
   - Uses `final_text || raw_text` as the source (respects user edits)
   - Re-chunks via the same `chunk_text()` the ingestion pipeline uses
   - Writes `chunk_count` only when it differs from the stored value
   - Returns a `BackfillResult` dataclass with `updated / unchanged / skipped / errors`

The command is deterministic, idempotent, and does not touch ChromaDB or the embeddings provider — so it runs without API keys and can be re-run safely. To rebuild embeddings as well, re-ingest via `update_entry_text()` (the existing PATCH path does chunk + embed + store in one call).

## Testing

New `tests/test_services/test_backfill.py` covers:
- Basic single-entry backfill from `raw_text`
- `final_text` takes precedence over `raw_text` when present
- Idempotence (second run reports `unchanged`, not `updated`)
- Skipping entries with no text at all
- Long text producing multiple chunks
- Chunker exceptions captured in `BackfillResult.errors` rather than crashing
- Dataclass defaults

The full suite went from 158 → 166 passing tests. Ruff is clean.

## Verification against the live dev DB

After the fix:

```bash
DB_PATH=.local-journal.db uv run journal seed
# Created entry 1: 2026-03-15 (ocr, 64 words, 1 chunks)
# ...
DB_PATH=.local-journal.db uv run journal backfill-chunks
# Updated:   0
# Unchanged: 5
# Skipped:   0 (no text)
```

And hitting the live REST API confirmed every entry now returns `chunk_count >= 1`.

## Incidental fixup

The `.venv` in journal-server had stale shebangs pointing at `/Users/john/projects/journal/.venv/bin/python3` (from when the project lived at the parent directory before the rename). Running `pytest` failed with `ModuleNotFoundError: 'journal.config'` until I deleted and re-created the venv with `uv sync`. Not committing anything for this — it's a developer-local artifact — but worth noting if the same error shows up again.

## Post-deploy verification on the media VM

After GitHub Actions built and pushed the new image, the backfill was run against the real production database:

```bash
docker exec journal-server uv run journal backfill-chunks
# Updated:   2
# Unchanged: 0
# Skipped:   0 (no text)
```

So there were exactly 2 entries in the production DB with stale `chunk_count = 0`, both now correct. First attempt used `docker exec journal-server journal backfill-chunks` (no `uv run`), which failed with `executable file not found in $PATH` because the `journal` script lives in `/app/.venv/bin` and isn't on the image's system PATH. Documented the working pattern in `docs/development.md` under *Backfilling chunk_count → Running the backfill in production*.

### Also noticed

The repo's `docker-compose.yml` declares `container_name: journal`, but the actual production container on the media VM is `journal-server`. The VM is running a different compose file than what's checked into the repo. Worth aligning at some point — either update the repo to use `journal-server` (to match prod), or update the prod compose to use `journal` (and update the docs I just wrote). Not fixed in this commit — flagged for a follow-up.
