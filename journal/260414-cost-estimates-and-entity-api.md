# Cost Estimates Documentation & Entity Extraction API

**Date:** 2026-04-14

## What Changed

### external-services.md overhaul
Reviewed and corrected cost estimates throughout the document:
- Fixed OCR token counts in the cost table — was using 660 input/220 output per page (missing
  image tokens), corrected to 2,100 input/800 output matching the per-page estimates section.
  This raised total lifecycle cost from ~$0.07 to ~$0.10 per 3-page entry.
- Fixed OCR provider designation — Gemini 3 Pro is now marked as primary, Opus as switchable
  alternative (was reversed).
- Fixed voice note cost typo ($0.001/min → $0.006/min).
- Clarified querying section: the embedding call goes to OpenAI, ChromaDB cosine search is local.
- Updated provider cost breakdown percentages (Anthropic 56%, Google 42%, OpenAI 2%).
- Updated pipeline diagram costs to match corrected estimates.

### New: Cost Comparisons section
Added a dedicated section comparing:
- Gemini 3 Pro vs Claude Opus 4.6 for OCR (~55% cheaper with Gemini)
- Voice transcription vs handwriting OCR (voice is 2-5x cheaper)
- Cost of editing/reviewing an entry (~$0.03, dominated by entity re-extraction)

### /api/settings — entity extraction config
Added `entity_extraction` block to the settings API response, exposing model name and dedup
similarity threshold. Previously this config was invisible to the frontend.

## Why
The documentation had several inconsistencies (OCR token counts, provider designation, voice
note pricing) that would mislead anyone using it for cost planning. The entity extraction config
was needed for the webapp's new pipeline-aligned settings view.
