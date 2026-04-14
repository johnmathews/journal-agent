# External Services & LLM Reference Document

Created `docs/external-services.md` — a comprehensive reference cataloguing every external service and AI/LLM
integration used or available to the Journal Analysis Tool.

## What it covers

The document is organised by processing stage (Ingestion, Enrichment, Embedding, Query) and for each task shows:

- Current provider and model in use
- All alternatives from OpenAI, Anthropic, Google, and Mistral with pricing, context windows, and benchmarks
- Self-hostable models that fit within CPU-only / <3 GB RAM constraints
- Per-task recommendations ranked by quality, value, and budget tiers
- Cost optimisation strategies (batch APIs, prompt caching, free tiers, right-sizing)

## Key research findings

- Gemini 3 Pro scores 100% on cursive handwriting benchmarks (vs ~91% for Claude Sonnet 4.5), at lower cost
- Self-hosted transcription is viable: whisper.cpp large-v3-turbo-q5_0 fits in ~1.2 GB with ~3% WER
- Self-hosted embeddings are viable: nomic-embed-text-v1.5 at 81 MB Q4 matches text-embedding-3-small quality
- NuExtract 3.8B is a purpose-built extraction model that fits in ~2.3 GB and matches GPT-4o extraction quality
- Self-hosted OCR is not yet viable at <3 GB for cursive handwriting
- Summarisation identified as a useful planned task (individual entries + weekly/monthly digests)

## Flaky test fix: test_patch_text_queues_mood_scoring

The CI run for the docs commit exposed a flaky test. `TestPatchMoodScoring::test_patch_text_queues_mood_scoring`
intermittently failed because the test's SQLite connection was missing `PRAGMA busy_timeout=5000`.

Root cause: the PATCH handler submits three background jobs (reprocess-embeddings, entity-extraction, mood-scoring)
via a single-worker `ThreadPoolExecutor`. The `jobs.create()` call in the request thread and the `mark_running()` call
in the executor thread both write to SQLite. Without `busy_timeout`, the loser of the write lock gets `SQLITE_BUSY`
immediately instead of retrying — and the PATCH handler's `try/except` silently swallows it, leaving `mood_job_id`
as `None`.

Fixed by replacing manual `sqlite3.connect()` + PRAGMA calls with `get_connection()` in 4 test files
(`test_api.py`, `test_api_ingest.py`, `test_api_jobs.py`, `test_mcp_server.py`). This ensures test connections
mirror production PRAGMAs exactly. Verified with 20 consecutive runs — all pass.

## Motivation

Needed a single reference showing which LLMs power which pipeline stages, what alternatives exist across providers,
and what could be self-hosted. This informs future provider decisions and cost optimisation.
