# OCR Context Files

This directory holds markdown files that are loaded once at server
startup and injected into the OCR system prompt to prime Claude with
known proper nouns. Editing a file here requires a server restart;
the content is cached on the Anthropic side via `cache_control` so
the marginal cost per OCR call is near zero once the cache is warm.

**The feature is opt-in.** The server only reads this directory if
`OCR_CONTEXT_DIR` points at it in the environment. Unset env var →
behaviour is identical to the pre-feature adapter.

## What to put here

Markdown files, one per category. The filename stem becomes the
section heading when the files are concatenated (underscores and
dashes become spaces), so pick descriptive names:

- `people.md` — family, friends, recurring characters
- `places.md` — cities, neighbourhoods, cafés, venues
- `topics.md` — recurring themes, projects, code names, hobbies
- `glossary.md` — anything else (medications, book titles, jargon)

Format each entry as a bullet with the canonical spelling and any
alternative spellings or OCR-failure modes you've seen, for example:

```markdown
- Ritsya — my daughter (also written "Ritzya", "Ritsa")
- Blue Bottle Coffee — café in North London; sometimes just "Blue Bottle"
```

## The minimum-size gotcha

Anthropic's prompt cache has a **4,096-token minimum**. If your
composed context is smaller than that, `cache_control` is silently
ignored and you pay full input cost on every OCR call. The server
logs a loud `WARNING` at startup when this happens — watch for:

> OCR system text is N tokens (approx) — below the 4096-token cache
> minimum for claude-opus-4-6.

Mitigations, in order:

1. Add more files / more entries to cross the threshold.
2. Accept the un-cached cost (~$0.01 per OCR call at today's Opus
   input pricing) and move on.

## Hallucination warning

The OCR provider prepends a strong anti-hallucination instruction
alongside the context, explicitly telling the model to **only**
prefer a glossary spelling when the handwritten token is visually
consistent with it. The failure mode we're avoiding is the model
"correcting" an ambiguous scribble to a glossary entry that isn't
actually what was written.

After enabling context priming, spot-check your first ~20 OCR
outputs against a sample run with the feature disabled to confirm
you're getting accuracy gains, not plausible-sounding fabrications.

See `docs/ocr-context.md` for the full design rationale, API
mechanism, cost impact, and risks.

## Git

Files in this directory are **deliberately not checked into the
repo**. The `.gitignore` excludes everything here except this
README. The content is personal (names, places, family details) and
should stay local. Treat it like you'd treat `.env`.
