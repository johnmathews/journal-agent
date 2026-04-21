# Sentinel-based dual-pass OCR reconciliation

**Date:** 2026-04-21

## Problem

Dual-pass OCR was producing 62 doubts per page (up from 2-3 with single-pass). The previous
reconciliation algorithm marked every word-level disagreement between the two OCR engines as an
uncertain span. Since two different engines naturally disagree on punctuation, spacing, and minor
readings, this flooded the review UI with noise, making dual-pass worse than single-pass.

## Solution

Replaced the word-diff-based reconciliation with a sentinel-based approach. Each model
independently flags its own uncertain regions with sentinels. The reconciler now only creates
doubts when at least one model used sentinels:

- **Primary uncertain, secondary confident** → substitute secondary text, mark as doubt
- **Secondary uncertain, primary confident** → keep primary text, mark as doubt
- **Both uncertain** → keep primary, mark as doubt
- **Neither uncertain** → keep primary, **no doubt** (trust the primary)

Text substitution required building the output text incrementally and tracking coordinate shifts
so spans after a substitution land at the correct positions.

## Key decisions

- Confident disagreements are resolved silently in favour of the primary. The tradeoff is that
  a rare confident-but-wrong primary reading won't be flagged. In practice this is far better
  than 60 false doubts drowning real ones.
- Disagreement blocks are treated as units (not per-word). If any word in a block has a sentinel,
  the whole block is affected. This avoids complex per-word alignment within disagreement blocks.

## Files changed

- `src/journal/providers/ocr.py` — rewrote `reconcile_ocr_results()`, added `_any_span_overlap()`,
  updated `DualPassOCRProvider` docstring
- `tests/test_providers/test_ocr.py` — rewrote `TestReconcileOcrResults` (16 tests covering all
  cases), updated `TestDualPassOCRProvider`
- `docs/ocr-context.md` — added "Dual-pass reconciliation" section
