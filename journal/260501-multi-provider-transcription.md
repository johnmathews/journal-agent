# 2026-05-01 — Multi-provider transcription pipeline

## What

Voice transcription is now provider-pluggable. The default behaviour is unchanged from the
single-provider era — `gpt-4o-transcribe` runs first — but the stack now sits behind a
`build_transcription_provider()` factory that composes a primary adapter with optional
retry+fallback and shadow wrappers from env-var config. Gemini (`gemini-2.5-pro`) is supported
as an alternative primary, `whisper-1` is the default fallback after retries, and a parallel
shadow adapter can be enabled to log word-level diffs between two providers for offline
evaluation.

## Why

Two long-standing limitations of the single-provider design forced the change:

1. The OpenAI `/audio/transcriptions` endpoint exposes spelling bias only via a 200-token
   `prompt` parameter — there's no `system_instruction` path, so the OCR context glossary can
   only nudge proper-noun spelling, not actually instruct the model. Gemini's audio API
   accepts a real system instruction and can take the entire glossary verbatim. Adding the
   Gemini adapter unlocks a strictly stronger glossary surface for users with large name
   dictionaries.
2. Single-provider outages stranded ingestion. A 5xx or rate-limit at OpenAI used to surface
   as an unrecoverable job failure. The retry+fallback wrapper makes transient failures
   absorb-able with no operator action and `whisper-1` as a dependable last line of defence.

The shadow wrapper closes the loop on provider evaluation: when deciding whether to switch
primaries, run both for a week on real audio and pick the winner from logged diffs instead of
vendor benchmarks.

## How

Seven commits, smallest-blast-radius first:

- **Refactor transcription context + rename provider class** (`5d88099`) — splits the
  context-files reader into a thin module exposing `build_whisper_prompt` (the existing
  200-token-capped string for OpenAI) and a new `build_full_context_instruction` (the full
  glossary as a system instruction for Gemini). Renames `OpenAITranscriptionProvider` →
  `OpenAITranscribeProvider` to align with the symmetry of `GeminiTranscribeProvider`.
- **Add Gemini transcription provider** (`bbd3fb8`) — new `GeminiTranscribeProvider` adapter
  using `google-genai` (pinned to 1.73.0). Default model `gemini-2.5-pro`. Uses structured
  output schemas to return `(text, uncertain_phrases)` in one call; the phrases are located
  in the transcript by `str.find` to produce uncertain spans.
- **Add RetryingTranscriptionProvider** (`68da5dd`) — exponential backoff (1s/2s/4s, capped
  at 30s, 3 attempts max) plus a final fall-through to `whisper-1`. Transient classifier
  covers `openai.APITimeoutError/APIConnectionError/RateLimitError/InternalServerError`,
  `google.genai.errors.ServerError` + `ClientError` (only `code==429`), and httpx
  `TimeoutException`/`ConnectError`.
- **Add ShadowTranscriptionProvider** (`6018acc`) — `ThreadPoolExecutor` runs primary and
  shadow in parallel; the primary's result is returned, a structured INFO log
  (`transcription_shadow_diff`) carries lengths, similarity ratio, uncertain-span counts, and
  a list of disagreeing word-level chunks from `difflib.SequenceMatcher.get_opcodes()`. **Full
  transcripts are deliberately not logged.**
- **Wire multi-provider via build_transcription_provider** (`c857f6c`) — factory that reads 9
  new env vars (`TRANSCRIPTION_PROVIDER`, `TRANSCRIPTION_FALLBACK_ENABLED`,
  `TRANSCRIPTION_FALLBACK_MODEL`, `TRANSCRIPTION_RETRY_MAX_ATTEMPTS`,
  `TRANSCRIPTION_RETRY_BASE_DELAY`, `TRANSCRIPTION_RETRY_MAX_DELAY`,
  `TRANSCRIPTION_SHADOW_PROVIDER`, `TRANSCRIPTION_SHADOW_MODEL`, plus the existing
  `TRANSCRIPTION_MODEL`) and assembles `Shadow(Retrying(Primary, fallback))`. Includes a
  cross-provider model-leak guard: `TRANSCRIPTION_MODEL=gpt-4o-transcribe` with
  `TRANSCRIPTION_PROVIDER=gemini` is silently overridden to the gemini default with an INFO
  log, rather than failing or making a doomed API call.
- **Cross-cutting integration tests** (`7de8f44`) — exercises stack composition end-to-end:
  retry sleeps the right number of times, fallback fires when the retry budget exhausts,
  shadow logs the right structured payload, etc. Lifts the transcription-module coverage to
  97%.
- **Expand /api/settings transcription block** (`81d38b4`) — surfaces the resolved provider,
  model, retry/fallback flags, and shadow stack to the webapp's Settings page so an operator
  can read the current state without `ssh`-ing in to inspect env vars.

## Tests

`tests/test_providers/test_transcription.py` — adapter and wrapper unit tests (Gemini schema
shape, transient classifier, retry budget, fallback wiring, shadow diff log fields, no
full-transcript leak).

`tests/test_providers/test_transcription_factory.py` — full-stack composition tests covering
all env-var combinations, including the cross-provider model-leak guard.

`tests/test_providers/test_transcription.py` reaches 97% coverage on
`src/journal/providers/transcription.py`. Total backend suite: **1498 passed**.

## Out of scope

- **Switching to OpenAI Chat Completions for instruction-following.** Would let
  `gpt-4o-transcribe` accept a real system prompt (as audio-input chat completions do)
  instead of the 200-token bias parameter. Considered and deferred — the endpoint, response
  shape, and logprobs surface differ, so it's a bigger refactor than swapping URLs. Gemini
  fills the same gap today.
- **Async transcription via batch / dynamic-batch APIs.** Both OpenAI and Gemini offer
  cheaper batch tiers for non-latency-sensitive ingestion. The current pipeline is
  request-thread synchronous; making it batchable means restructuring the job runner around
  an "audio uploaded → transcript pending" intermediate state.
- **Persisting shadow diffs to SQLite.** Currently they live only in the structured-log
  stream. A future evaluation harness could pull them into a dedicated table for
  per-provider WER metrics and per-name accuracy tracking. Until shadow mode is actually
  used in anger, the structured logs are enough.
- **Cost meter that splits primary vs shadow vs fallback spend** in the dashboard. The
  current `pricing` table only knows about a single transcription model.
