# Fix Gemini OCR model name and update pricing

## What changed

The default Gemini OCR model was set to `gemini-3-pro`, which is not a valid model ID —
Google deprecated "Gemini 3 Pro Preview" and shut it down on 2026-03-09. Ingestion via
Gemini OCR was completely broken (404 NOT_FOUND from the Google API).

Changed the default to `gemini-2.5-pro`, which is the current stable Gemini Pro model.
The model remains overridable via the `OCR_MODEL` environment variable.

Updated cost estimates in `docs/external-services.md` to reflect `gemini-2.5-pro` pricing
($1.25/$10 per MTok vs $2/$12), which drops OCR cost from ~$0.042 to ~$0.032 per 3-page
entry and total pipeline cost from ~$0.10 to ~$0.09.

## Files changed

- `src/journal/providers/ocr.py` — default param + `_DEFAULT_MODELS` dict
- `src/journal/config.py` — comment
- `tests/test_providers/test_ocr.py` — test fixture model name
- `docs/configuration.md` — two model name references
- `docs/external-services.md` — all model references + pricing + cost totals
