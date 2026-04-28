# 2026-04-28 — Context-driven Whisper priming + date-heading detection

## What

Two behaviour changes, one new public surface:

1. **Date-heading detection.** A new `services/heading_detector.py` runs after OCR and after voice transcription.
   When the leading text looks like a date (numeric, spelled-out, ordinal, "today/yesterday", optional trailing
   time), it gets lifted into a markdown `# ` heading on `final_text`. `raw_text` is preserved verbatim. Anthropic
   Claude Haiku is the detector — pure regex was rejected as too brittle for the spectrum of forms that show up in
   spoken entries ("the twenty-eighth of April twenty twenty-six"). When the detector fails (API error, malformed
   JSON, hallucinated source phrase), it returns `(no heading, original text)` and the pipeline continues.

2. **Whisper transcription context.** The user's existing `OCR_CONTEXT_DIR` markdown files now also drive Whisper.
   A new `services/transcription_context.py` reads the same `.md` files as OCR, strips markdown structure, collapses
   whitespace, and truncates at a token boundary to ≤200 tokens (Whisper's documented hard cap is 224; 200 leaves
   headroom). The composed prompt is built once at provider init and cached for the provider's lifetime.

3. **Public surface:** two new env vars (`DATE_HEADING_DETECTION`, `DATE_HEADING_MODEL`,
   `TRANSCRIPTION_CONTEXT_ENABLED`) and two new runtime-settings keys (`date_heading_detection`,
   `transcription_context_enabled`) so the toggles are editable from the admin UI without a restart. Edits to the
   underlying *files* still need a restart — same as OCR's existing behaviour.

## Why

For voice:

- Spoken proper nouns ("Adi", "Ritsya", "Hampstead Heath") were the dominant Whisper failure mode — phonetic
  spellings, mishears, plural transformations. The user already maintains a curated list of those names for OCR;
  reusing it for Whisper's `prompt` parameter is the smallest change that addresses the largest accuracy gap.

For both pipelines:

- The user explicitly speaks/writes the date at the start of an entry. Treating it as a heading was just a tagging
  decision — the model already knows what it is, we just weren't capturing the structure. Making it a markdown
  heading turns a date that's currently buried in body prose into a visible anchor in the entry view.

## Decisions worth remembering

- **Heading detector ordering vs. formatter.** The optional Anthropic paragraph formatter has a strict word-count
  preservation contract. Running heading detection BEFORE the formatter (and giving the formatter only the body)
  keeps that contract intact — the formatter never sees the leading `#` characters that would otherwise break its
  word-equality check.
- **Source-phrase verification.** The detector asks the LLM to return the exact verbatim substring that became the
  heading, not a character offset. We then check that the substring is a prefix of the input. This is bulletproof
  against the model hallucinating an offset, and lets us split on the model's actual claim rather than counting.
- **`raw_text` stays untouched.** Both new steps go through `final_text` only. The OCR overlay and audit trail
  still anchor to whatever the model originally returned.
- **Token cap is 200, not 224.** Tiktoken's `o200k_base` encoder is a close-but-not-identical approximation of
  OpenAI's actual Whisper tokenizer. 200 leaves enough headroom to be safe even with mild drift.

## Tests

- `tests/test_services/test_heading_detector.py` — 24 tests covering the protocol, null detector, all the date forms
  the user mentioned, mid-sentence dates, already-a-heading short-circuit, API error fallback, malformed JSON
  fallback, hallucinated source-phrase fallback, leading-whitespace input, and the 300-char detection window cap.
- `tests/test_services/test_ingestion.py::TestHeadingDetection` — 8 tests for both voice and OCR integrations,
  including the formatter-runs-on-body-only case.
- `tests/test_services/test_transcription_context.py` — 25 tests for the markdown stripper (headings, bullets,
  bold, italic, code, links, images, horizontal rules), the whitespace normalizer, the tiktoken truncator, and the
  prompt builder end-to-end.
- `tests/test_providers/test_transcription.py` — adds a forwarding test confirming the OpenAI client receives
  `prompt=` when set, and does NOT receive it when blank (preserves prior behaviour).

Full backend suite: 1405 passed.

## Follow-ups deferred

- The user has not yet edited their context files for Whisper-specific tuning. We'll see how Whisper actually
  reacts to the existing OCR-shaped content before recommending changes (e.g. shorter entries, more aliases, no
  prose).
- No regex pre-check in the heading detector. Each ingest now incurs an extra ~300-500ms Haiku call. If a high
  ingestion volume makes that visible, a fast path for obvious date forms would shave latency without losing the
  semantic detection for the spelled-out cases.

## Files

- `src/journal/services/heading_detector.py` (new)
- `src/journal/services/transcription_context.py` (new)
- `src/journal/services/ingestion.py` (heading-detector integration)
- `src/journal/providers/transcription.py` (Whisper `prompt` forwarding)
- `src/journal/services/runtime_settings.py` (two new keys)
- `src/journal/config.py` (three new env-var settings)
- `src/journal/mcp_server.py` (provider construction + runtime-settings change handler)
- `context/README.md` (rewritten — covers OCR + voice)
- `docs/context-files.md` (new — unified user-facing reference)
- `docs/configuration.md` (new env-var sections)
- `.env.example` (new env vars)
