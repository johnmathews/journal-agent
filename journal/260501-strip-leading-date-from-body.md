# 2026-05-01 — Strip leading date from entry body (instead of lifting to a heading)

## What

Behaviour change to ingestion. When the heading detector identifies a leading date in OCR or
transcribed audio, the date is now **stripped entirely** from `final_text` rather than lifted
into a markdown `# ` heading.

Affects all four ingestion paths:

- `ingest_image` (single-page OCR)
- `ingest_voice` (single audio recording)
- `ingest_multi_page_entry` (multi-page OCR)
- `ingest_multi_voice` (multi-clip audio)

The Anthropic-backed `AnthropicHeadingDetector` is unchanged — it still does the heavy lifting
of recognising spelled-out dates, ordinals, relative phrases ("today", "yesterday"), and dates
with trailing times. What changed is what we do with its output: previously
`HeadingDetectionResult.to_text()` reconstructed `# heading\n\nbody`; now ingestion writes only
`body` (which the detector has already stripped of the leading date phrase).

`raw_text` is unchanged — verbatim OCR / transcription is preserved for audit.

## Why

The webapp displays the entry's date as the page title (the big "30 April 2026" header).
A markdown `# 30 April 2026` heading at the top of the body just duplicates the title — the
user sees the same date twice. Stripping the date from the body removes the duplication while
keeping the underlying detection model intact for any future use (e.g. picking up the time of
day if dictated).

The user noted this is most common with audio entries — verbal dictations frequently start
with the date because that's how you'd open a voice note. OCR'd handwritten pages do it too,
just less often.

## How

1. Three `final_text = det.to_text() if det.has_heading else None` lines became
   `final_text = det.body if det.has_heading else None` (image + multi-page).
2. The two voice paths constructed a temporary `HeadingDetectionResult(...).to_text()` purely
   to recombine heading + body — that whole step is gone. `final_text = formatted_body` directly.
3. The dataclass and `to_text()` method on `HeadingDetectionResult` remain (still used by
   detector tests) — only ingestion-side usage changed.

No backfill of the existing 44 entries — new ingestions only.

## Tests

The five existing assertions in `TestHeadingDetection` that expected `# 28 April 2026\n\nbody`
were flipped to expect just `body`. Each gained a `not entry.final_text.startswith("#")`
assertion to pin the intent. Test names were updated from
`*_writes_heading_to_final_text_only` → `*_strips_date_from_final_text`.

The `test_detector_combines_with_formatter_on_body_only` test still asserts the formatter
sees only the body — unchanged behaviour, just no markdown heading prepended afterward.

## Out of scope

- Backfill of existing entries via `update_entry_text` — user opted to leave them.
- Removing the read-side fallback at `repository.py:291` (`row["final_text"] or row["raw_text"]`),
  which makes an empty `final_text` fall back to `raw_text` on read. A truly date-only voice note
  would currently still surface its raw date when read back, but that case is vanishingly rare
  and the broader fallback exists by design.
