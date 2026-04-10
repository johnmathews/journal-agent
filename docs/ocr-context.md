# OCR Context Priming

The OCR provider can be primed with a static set of "context files"
that tell Claude about known proper nouns in the author's life —
family names, places, recurring topics — so that handwritten tokens
that match those names are transcribed correctly. This document
explains the mechanism, the cost profile, the failure modes, and how
to enable it.

## Why

Claude's vision OCR is very good at English handwriting in the
general case, but it has no way to know that "Ritsya" is a name
rather than a typo. Without priming, personal names, place names,
and jargon are where OCR errors cluster. A glossary supplied in the
system prompt gives the model candidates to prefer when the pen
strokes are consistent with a known name.

This is a principled application of Anthropic's `cache_control`
feature to a specific problem. As of April 2026 there are **no
published case studies** of glossary priming for handwriting OCR,
so treat the first week of deployment as an experiment and measure.

## How it's built

### API mechanism

Anthropic's Messages API `system` parameter accepts an array of text
blocks, each of which can be independently cacheable via
`cache_control`. The OCR adapter composes a single text block at
construction time:

```
SYSTEM_PROMPT
<hallucination prevention instructions>

# people
- Ritsya — daughter (also "Ritzya", "Ritsa")
- ...

# places
- Blue Bottle — café in North London
- ...
```

and marks it with
`{"type": "ephemeral", "ttl": "1h"}` so Anthropic caches it for one
hour. See https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching
for the current caching documentation.

### File format

Plain markdown, one file per category, inside `context/`:

- `context/people.md`
- `context/places.md`
- `context/topics.md`
- `context/glossary.md`

The filename stem becomes a section heading when the files are
concatenated (underscores and dashes become spaces), so pick
descriptive names. Files are loaded in alphabetical order for
determinism.

### Loading

The provider reads `context/` **once at startup** in
`AnthropicOCRProvider.__init__`. Editing a file requires a server
restart. Hot-reload was considered and rejected: a single-user tool
has near-zero restart cost, and deterministic cache behaviour (cache
key = process lifetime) is easier to reason about than
"restart-sometimes" semantics.

### Enabling it

Set `OCR_CONTEXT_DIR` in the environment — the only signal that turns
the feature on:

```bash
# .env
OCR_CONTEXT_DIR=./context
OCR_CONTEXT_CACHE_TTL=1h  # or "5m"
```

When `OCR_CONTEXT_DIR` is unset (the default), the OCR adapter
behaves identically to the pre-feature version — the same
`SYSTEM_PROMPT` is used, with no appended instructions or glossary.

## The 4,096-token minimum

Anthropic's prompt cache has a minimum block size of **4,096 tokens**
on Claude Opus 4.6 (confirmed against the current docs on 2026-04-10).
If the composed system block is smaller, `cache_control` is **silently
ignored** and every request pays full input price — no error, no
warning from Anthropic.

The provider counts tokens at startup using `tiktoken.get_encoding
("cl100k_base")` (a close-enough proxy for Claude's tokenizer, which
Anthropic does not ship offline) and emits a **loud WARNING** when the
block is below the minimum:

> OCR system text is 1247 tokens (approx) — below the 4096-token
> cache minimum for claude-opus-4-6. cache_control will be silently
> ignored and every request will pay full input price.

Mitigations:

1. **Add more files / more entries** until the composed block crosses
   4,096 tokens.
2. **Accept the un-cached cost** — at today's Opus input pricing, a
   ~2,000-token context adds ~$0.01 per OCR call, which is tolerable
   for personal use but noticeable in a batch run.

## Cost impact

Numbers based on the Anthropic pricing multipliers published at
https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching
(cache-write 2× at 1-hour TTL, cache-read 0.1×, base input 1×):

Assume a 4,200-token composed system block (just above the cache
minimum) and ~$5/MTok base Opus input pricing.

| Scenario | Tokens billed | Cost |
|---|---|---|
| Cold start / first call of hour (cache write) | 4,200 × 2× | ~$0.042 |
| Cached call within hour (cache read) | 4,200 × 0.1× | ~$0.002 |
| No caching (block too small) | 4,200 × 1× | ~$0.021 |

Break-even: after **2 cached hits**, the cost to cache-write the
block is already amortized below its un-cached equivalent. Any
ingestion session doing more than 3–4 OCR calls per hour comes out
ahead with 1-hour TTL.

## Hallucination risk

The biggest risk is **hallucinated substitution**: the model
"correcting" an ambiguous scribble to a glossary entry that isn't
actually what was written. Without a mitigation, adding "Ritsya" to
the glossary would tempt the model to transcribe any vaguely similar
squiggle as "Ritsya", even when the page actually said "Rita" or a
stranger's name. Errors would cluster on known entities, making them
plausible and hard to spot.

The mitigation is a strongly-worded instruction that the adapter
prepends to the glossary (see `CONTEXT_USAGE_INSTRUCTIONS` in
`src/journal/providers/ocr.py`):

> The sections below contain proper nouns (people, places, topics)
> that appear frequently in this author's handwritten journal. Use
> them as a candidate list ONLY — prefer a glossary spelling when
> the handwritten token is visually consistent with the entry, but
> do NOT substitute for the sake of matching. If a word is ambiguous
> AND does not match any glossary entry, transcribe exactly what you
> see, even if it looks like a typo. Never invent a glossary match
> that isn't supported by the pen strokes on the page.

After enabling the feature, **spot-check your first ~20 OCR outputs**
against a run with the feature disabled to confirm you're getting
accuracy gains rather than plausible-sounding fabrications. The
anti-hallucination instruction is necessary but not sufficient — the
ground truth is your eyes on the pages.

## Other risks and mitigations

- **Short-entry dilution.** A one-sentence handwritten page with 4k
  tokens of context in front of it is lopsided. In practice Claude
  handles this fine because the user-turn instruction
  (`"Extract all handwritten text from this image."`) narrows the
  task. If you notice summarisation or interpretation creeping in,
  strengthen the user-turn instruction further.
- **Cache invalidation by image churn.** Image blocks that change
  from request to request invalidate any downstream cached blocks.
  Because the context sits in the `system` array (which is sent
  before the user turn with the image), the cache survives.
- **Cache-miss cost surprises.** If the user edits context files
  and restarts frequently, cache writes (2× multiplier) dominate.
  At ~$0.04 per cold start this is negligible but worth knowing.

## Files to read if you change this

- `src/journal/providers/ocr.py` — the adapter and `load_context_files`
- `src/journal/config.py` — `ocr_context_dir`, `ocr_context_cache_ttl`
- `tests/test_providers/test_ocr.py` — full test suite including
  context composition, cache warning, TTL validation, file loading
- `context/README.md` — user-facing guidance on what to put in the
  directory
- `.env.example` — how to enable the feature via env vars
