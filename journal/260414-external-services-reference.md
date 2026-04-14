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

## Motivation

Needed a single reference showing which LLMs power which pipeline stages, what alternatives exist across providers,
and what could be self-hosted. This informs future provider decisions and cost optimisation.
