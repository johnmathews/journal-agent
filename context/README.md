# Context Files

This directory holds markdown files that prime two pipelines with the proper nouns and unusual terms that show up in
your journal:

1. **OCR** — concatenated and injected into the vision model's system prompt so handwritten names get transcribed
   correctly (Anthropic + Gemini providers, both supported).
2. **Voice transcription** — stripped of markdown, truncated to ~200 tokens, and passed to OpenAI Whisper as the
   `prompt` parameter so spoken names get spelled correctly (added 2026-04-28).

A single set of files drives both. Editing a file requires a server restart; the OCR side is cached on the Anthropic
side via `cache_control` so the marginal cost per OCR call is near zero once the cache is warm.

**The feature is opt-in.** The server only reads this directory if `OCR_CONTEXT_DIR` points at it in the environment.
Voice transcription priming is on by default when context files are loaded but can be disabled with
`TRANSCRIPTION_CONTEXT_ENABLED=false`.

## What to put here

Markdown files, one per category. The filename stem becomes the section heading when the files are concatenated for
OCR (underscores and dashes become spaces), so pick descriptive names:

- `people.md` — family, friends, recurring characters
- `places.md` — cities, neighbourhoods, cafés, venues
- `topics.md` — recurring themes, projects, code names, hobbies
- `glossary.md` — anything else (medications, book titles, jargon)

Format each entry as a bullet with the canonical spelling and any alternative spellings or transcription-failure modes
you've seen. Bold the name so the markdown stripper for the Whisper prompt keeps it prominent:

```markdown
# People

- **Adi** — close friend, lives in Berlin (NOT "Addy", "Eddie")
- **Dr. Patel** — physiotherapist (NOT "Doctor Patel" capitalised)
- **Ritsya** — daughter (also written "Ritzya", "Ritsa")
```

```markdown
# Places

- **Hampstead Heath** — park I run on
- **Blue Bottle Coffee** — café in North London (sometimes just "Blue Bottle")
- **Old Street** — London Underground station
```

```markdown
# Topics

- **journal-server** — Python backend in this monorepo
- **dual-pass OCR** — Anthropic + Gemini reconciliation strategy
- **HRV** — heart-rate variability
```

```markdown
# Glossary

- **PRAGMA user_version** — SQLite migration version mechanism
- **microcycle** — a 7-10 day training block
```

## What helps Whisper

The Whisper `prompt` parameter biases the model toward correct **spellings** of words it contains. So:

- ✅ Proper nouns and unusual words help — names, places, jargon, brands.
- ❌ Long-form prose ("Adi is my best friend from Berlin") doesn't help much — the prompt is a spelling prior, not a
  system instruction.

Bullet lists with bold names are ideal because the markdown stripper keeps the names and discards the surrounding
syntax, leaving a dense list of high-value terms.

## The 200-token Whisper cap

Whisper accepts a prompt of **up to 224 tokens**; the builder truncates at 200 to leave headroom. If your composed
context exceeds 200 tokens, the alphabetically-later entries are silently dropped from the **Whisper prompt only**.

If you're hitting the cap and certain entries are more important than others, reorder by name: `01-people.md`,
`02-places.md`, etc. The OCR pipeline always sees the full set regardless of token budget.

## The 4096-token OCR cache minimum

Anthropic's prompt cache has a **4,096-token minimum**. If your composed context is smaller than that, `cache_control`
is silently ignored and you pay full input cost on every OCR call. The server logs a loud `WARNING` at startup when
this happens — watch for:

> OCR system text is N tokens (approx) — below the 4096-token cache minimum for claude-opus-4-6.

Mitigations, in order:

1. Add more files / more entries to cross the threshold.
2. Accept the un-cached cost (~$0.01 per OCR call at today's Opus input pricing) and move on.

## Hallucination warning

Both pipelines include explicit anti-hallucination instructions. The OCR provider prepends a strong rule telling the
model to **only** prefer a glossary spelling when the handwritten token is visually consistent with it. The Whisper
prompt is a softer spelling bias — Whisper has a strong language-model prior and proper-noun context only nudges it.

After enabling either feature, spot-check your first ~20 OCR outputs and your first handful of voice transcriptions
to confirm you're getting accuracy gains, not plausible-sounding fabrications. If a name keeps getting auto-corrected
toward an entry that wasn't actually said/written, remove it from the file.

See `docs/context-files.md` for the unified reference and `docs/ocr-context.md` for the full OCR design rationale.

## Git

Files in this directory are **deliberately not checked into the repo**. The `.gitignore` excludes everything here
except this README. The content is personal (names, places, family details) and should stay local. Treat it like
you'd treat `.env`.
