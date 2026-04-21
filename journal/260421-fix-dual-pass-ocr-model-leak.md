# Fix dual-pass OCR model name leak

**Date:** 2026-04-21

## Problem

Image ingestion failed in production with:

```
anthropic.NotFoundError: 404 - model: gemini-2.5-pro
```

The Anthropic API was receiving `gemini-2.5-pro` as the model name. This happened because
`OCR_MODEL` was set to `gemini-2.5-pro` (from earlier single-pass Gemini usage) and dual-pass
mode was later enabled via the runtime settings toggle. The `_build_dual_pass_provider` factory
passed `config.ocr_model` directly to the Anthropic primary provider without checking whether
the model was appropriate for that provider.

## Fix

In dual-pass mode, both providers now always use their provider-specific defaults
(`claude-opus-4-6` for Anthropic, `gemini-2.5-pro` for Gemini). `OCR_MODEL` is only
honoured in single-pass mode where it unambiguously applies to one provider.

## Files changed

- `src/journal/providers/ocr.py` — `_build_dual_pass_provider` now uses `_DEFAULT_MODELS`
  directly instead of `config.ocr_model or _DEFAULT_MODELS[...]`
- `tests/test_providers/test_ocr.py` — added `test_dual_pass_ignores_ocr_model_override`
- `docs/configuration.md` — noted that `OCR_MODEL` is ignored in dual-pass mode

## Notes

This is the second time a model name issue has caused OCR failures (the first was
`gemini-3-pro` → `gemini-2.5-pro` on 2026-04-14). A future improvement could validate
model names against provider-specific patterns at startup.
