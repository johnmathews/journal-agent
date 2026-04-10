# 2026-04-10 — Multi-page URL ingestion

## Context

First real end-to-end test of the deployed stack: uploaded two photos of a
single handwritten journal entry via a Slack-driven agent that speaks to the
MCP server. Expected one entry with two pages. Got two separate entries (ids
3 and 4, both dated 2026-04-10, one per photo).

Root cause in the logs was unambiguous:

```
16:29:32 journal_ingest_from_url(source_type=image, url=.../img_3072.jpg) → entry 3
16:29:58 journal_ingest_from_url(source_type=image, url=.../img_3073.jpg) → entry 4
```

The agent did the only thing it could: call the single-image URL ingest tool
twice. `IngestionService.ingest_multi_page_entry` already existed, and so did
the matching `journal_ingest_multi_page` MCP tool, **but** the multi-page tool
only accepted base64 blobs. An agent handing URLs to the server had no way to
reach the multi-page path.

## Change

1. `IngestionService.ingest_multi_page_entry_from_urls(urls, date, media_types=None)`
   — downloads each URL (respecting Slack bearer auth), then delegates to the
   existing `ingest_multi_page_entry`. Thin wrapper by design; the OCR,
   chunking, embedding, and storage logic is unchanged.
2. New MCP tool `journal_ingest_multi_page_from_url` exposing it.
3. Docstring nudge on `journal_ingest_from_url`: now explicitly tells callers
   that multiple photos of the same entry must use the multi-page variant,
   not repeated single-page calls.
4. `docs/api.md` documents the new tool and corrects the stale entry for
   `journal_ingest_multi_page` (it claimed to take `urls`, but the actual
   signature is `images_base64` + `media_types`).
5. Tests in `test_services/test_ingestion_url.py` cover the happy path,
   per-URL `media_type` overrides, Slack bearer auth propagation, empty-URL
   and length-mismatch validation, and the duplicate-hash rejection path
   (which fires on previously-ingested files, not within-batch duplicates —
   see note below).

## Alternatives considered

- **`entry_id` append parameter on the single-image tool.** More flexible but
  pushes correctness onto the agent: every caller has to remember to chain
  calls. The multi-page-from-urls tool keeps the contract batch-oriented,
  which matches how the failure actually happened.
- **Server-side auto-merge** of same-date ingests. Tempting because it would
  have caught this case with zero client changes, but it silently destroys
  the legitimate "two separate entries on one day" scenario. Rejected.

## Note on duplicate detection

`_is_duplicate` reads `source_files`, so it only catches *previously persisted*
hashes. Two identical images within the same multi-page batch will **not**
trip it — they'll both be OCR'd and written as pages of the same entry. That's
arguably a separate bug but I'm leaving it alone for now: within-batch
duplicates are unlikely from a real multi-page upload, and fixing it cleanly
means threading an in-flight hash set through `ingest_multi_page_entry`.

## Cleanup

The two orphan entries from the failed upload (ids 3 and 4) are still in the
prod DB. I'll delete them via the UI and re-ingest the pages as one entry
using the new tool once it's deployed.

## Review-phase tweak

During /done's code review, I noticed the MCP tool's `media_types`
parameter was typed `list[str] | None` while the docstring claimed
"entries may be null/None". The service layer genuinely supports
per-element `None` (each null falls back to the response
`Content-Type` header), but the MCP boundary never exposed that:
callers either omit `media_types` entirely or pass a full `list[str]`.
Tightened the docstring to match the type signature rather than
widening the type, since per-element overrides are a YAGNI feature
for URL-based ingestion (Slack and CDN Content-Type headers are
almost always correct).
