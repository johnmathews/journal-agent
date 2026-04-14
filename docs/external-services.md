# External Services & LLM Reference

This document catalogues every external service and AI/LLM integration used (or usable) by the Journal Analysis Tool. It
is organised by **processing stage** so you can see at a glance which models power which step of the pipeline and what
alternatives exist.

> **Last updated:** 2026-04-14

---

## What Happens When You Process a Single Entry

The table below shows **every external API call** in order, for the most common flow: uploading a handwritten page, then
later reviewing and correcting the OCR text.

### Upload a handwritten page image

| Step | What happens                                                                         | API call?                                          | Provider  | Model                    | Runs on        |
| ---- | ------------------------------------------------------------------------------------ | -------------------------------------------------- | --------- | ------------------------ | -------------- |
| 1    | **OCR** — image sent to vision LLM, text returned                                    | **Yes — 1 LLM call**                               | Anthropic | `claude-opus-4-6`        | Request thread |
| 2    | Date extraction from OCR text                                                        | No — regex/heuristic                               | —         | —                        | Request thread |
| 3    | Entry + page records saved to SQLite                                                 | No — local DB                                      | —         | —                        | Request thread |
| 4    | **Semantic chunking** — sentences embedded, cosine similarity finds topic boundaries | **Yes — 1 embedding call** (batch)                 | OpenAI    | `text-embedding-3-large` | Request thread |
| 5    | **Chunk embedding** — final chunks embedded for vector search                        | **Yes — 1 embedding call** (batch)                 | OpenAI    | `text-embedding-3-large` | Request thread |
| 6    | Chunks + vectors stored in ChromaDB                                                  | No — local HTTP to ChromaDB                        | —         | —                        | Request thread |
| 7    | **Mood scoring** — text scored on 7 dimensions                                       | **Yes — 1 LLM call** (if enabled)                  | Anthropic | `claude-sonnet-4-5`      | Request thread |
| 8    | **Entity extraction** — queued as background job                                     | **Yes — 1 LLM call**                               | Anthropic | `claude-opus-4-6`        | Background job |
| 9    | Entity dedup — new entities compared to existing by name similarity                  | **Yes — 1 embedding call** (if new entities found) | OpenAI    | `text-embedding-3-large` | Background job |

**Total for upload: 4-6 API calls** (1 LLM for OCR + 2 embedding for chunking/storage + 1 LLM for entities + optionally 1
LLM for mood + optionally 1 embedding for entity dedup).

### Review and correct OCR text (PATCH save)

When you edit the entry text and save, the following re-processing is triggered:

| Step | What happens                                        | API call?                                                | Provider  | Model                    | Runs on        |
| ---- | --------------------------------------------------- | -------------------------------------------------------- | --------- | ------------------------ | -------------- |
| 1    | Updated text saved to SQLite                        | No — local DB                                            | —         | —                        | Request thread |
| 2    | **Re-chunk + re-embed** — queued as background job  | **Yes — 2 embedding calls** (chunk boundaries + storage) | OpenAI    | `text-embedding-3-large` | Background job |
| 3    | **Entity re-extraction** — queued as background job | **Yes — 1 LLM call**                                     | Anthropic | `claude-opus-4-6`        | Background job |
| 4    | Entity dedup (if new entities found)                | **Yes — 1 embedding call** (if needed)                   | OpenAI    | `text-embedding-3-large` | Background job |
| 5    | **Mood re-scoring** — queued as background job      | **Yes — 1 LLM call** (if enabled)                        | Anthropic | `claude-sonnet-4-5`      | Background job |

**Total for save: 3-5 API calls** (2 embedding + 1 LLM for entities + optionally 1 LLM for mood + optionally 1 embedding
for dedup).

### Full lifecycle total: 7-11 API calls

| Type                      | Upload  | Save    | Total    | Provider  |
| ------------------------- | ------- | ------- | -------- | --------- |
| Vision LLM (OCR)          | 1       | 0       | **1**    | Anthropic |
| LLM (entity extraction)   | 1       | 1       | **2**    | Anthropic |
| LLM (mood scoring)        | 0-1     | 0-1     | **0-2**  | Anthropic |
| Embedding (chunking)      | 1       | 1       | **2**    | OpenAI    |
| Embedding (chunk storage) | 1       | 1       | **2**    | OpenAI    |
| Embedding (entity dedup)  | 0-1     | 0-1     | **0-2**  | OpenAI    |
| **Total**                 | **4-6** | **3-5** | **7-11** |           |

### Other entry types

| Input type           | Difference from handwritten page                                                           |
| -------------------- | ------------------------------------------------------------------------------------------ |
| **Voice note**       | Step 1 is transcription (OpenAI `gpt-4o-transcribe`) instead of OCR. Same steps 2-9 after. |
| **Typed text**       | No step 1 (text provided directly). Same steps 2-9 after. Saves 1 API call.                |
| **Multi-page image** | Step 1 is N OCR calls (1 per page), concatenated into one entry. Same steps 2-9 after.     |

### Querying (no ingestion)

| Action            | API calls                                               |
| ----------------- | ------------------------------------------------------- |
| Semantic search   | 1 embedding call (embed query → ChromaDB cosine search) |
| Keyword search    | 0 (FTS5, local)                                         |
| Stats / dashboard | 0 (SQL aggregation, local)                              |

---

## Current Stack Summary

| Provider      | Model                    | Used for                            | Price                   |
| ------------- | ------------------------ | ----------------------------------- | ----------------------- |
| **Anthropic** | `claude-opus-4-6`        | OCR, entity extraction              | $5.00 / $25.00 per MTok |
| **Anthropic** | `claude-sonnet-4-5`      | Mood scoring                        | $3.00 / $15.00 per MTok |
| **Google**    | `gemini-3-pro`           | OCR (switchable alt)                | $2.00 / $12.00 per MTok |
| **OpenAI**    | `text-embedding-3-large` | Chunking, search, entity dedup      | $0.13 per MTok          |
| **OpenAI**    | `gpt-4o-transcribe`      | Voice transcription                 | $0.006 / min            |
| **ChromaDB**  | HNSW cosine              | Vector storage                      | Self-hosted             |
| **SQLite**    | FTS5                     | Structured storage + keyword search | Local                   |

---

## Stage 1: Ingestion

Raw input (images, audio) is converted to plain text.

### 1a. OCR (Handwriting Recognition)

Photos of handwritten journal pages are sent to a vision-capable LLM which returns the transcribed text. The current
implementation supports context-priming (a glossary of proper nouns injected into the system prompt) and marks uncertain
regions with `⟪/⟫` sentinels.

**Current:** Anthropic Claude Opus 4.6 (primary), Google Gemini 3 Pro (switchable alternative).

#### Cloud API Options

| Provider      | Model                 | Input $/MTok | Output $/MTok | Context | Batch   | Handwriting Score | Notes                                             |
| ------------- | --------------------- | ------------ | ------------- | ------- | ------- | ----------------- | ------------------------------------------------- |
| **Anthropic** | Claude Opus 4.6       | $5.00        | $25.00        | 1M      | 50% off | ~91%+             | Prompt caching for glossary. Current primary.     |
| **Anthropic** | Claude Sonnet 4.6     | $3.00        | $15.00        | 1M      | 50% off | ~91%              | Cheaper, similar quality for OCR.                 |
| **Anthropic** | Claude Haiku 4.5      | $1.00        | $5.00         | 200K    | 50% off | —                 | Untested on cursive; worth benchmarking.          |
| **Google**    | Gemini 3.1 Pro        | $2.00        | $12.00        | 1M      | 50% off | ~100%             | Top handwriting benchmark score.                  |
| **Google**    | Gemini 3 Pro          | $2.00        | $12.00        | 1M      | 50% off | 100%              | Current alt provider. Best-in-class cursive.      |
| **Google**    | Gemini 2.5 Pro        | $1.25        | $10.00        | 1M      | 50% off | 95%               | Strong quality, lower cost.                       |
| **Google**    | Gemini 2.5 Flash      | $0.30        | $2.50         | 1M      | 50% off | —                 | Budget option, quality TBD on cursive.            |
| **Google**    | Gemini 2.5 Flash-Lite | $0.10        | $0.40         | 1M      | 50% off | —                 | Ultra-budget. Free tier available.                |
| **OpenAI**    | GPT-5.4               | $2.50        | $15.00        | 1.1M    | 50% off | ~97%              | "Original" detail mode for full-fidelity images.  |
| **OpenAI**    | GPT-4.1               | $2.00        | $8.00         | 1M      | 50% off | ~97%              | Good value with 1M context.                       |
| **OpenAI**    | GPT-4.1 Mini          | $0.40        | $1.60         | 1M      | 50% off | —                 | Budget option, quality TBD on cursive.            |
| **OpenAI**    | GPT-4.1 Nano          | $0.10        | $0.40         | 1M      | 50% off | —                 | Cheapest OpenAI vision model.                     |
| **Mistral**   | Mistral Small 4       | $0.15        | $0.60         | 262K    | —       | 85%               | Cheapest multimodal model. Lower cursive quality. |
| **Mistral**   | Pixtral Large         | $2.00        | $6.00         | 131K    | —       | 85%               | Same vision engine as Small 4.                    |

**Handwriting benchmark source:** AIMultiple cursive handwriting recognition benchmark (100 samples, cosine semantic
similarity metric).

#### Cost per Page Estimates

Assuming a typical handwritten page: ~1,600 image tokens + ~500 system prompt tokens + ~800 output tokens.

| Model                   | Standard | Batch (50% off) |
| ----------------------- | -------- | --------------- |
| Claude Opus 4.6         | ~$0.031  | ~$0.015         |
| Claude Sonnet 4.6       | ~$0.018  | ~$0.009         |
| Claude Haiku 4.5        | ~$0.006  | ~$0.003         |
| Gemini 3 Pro (<=200K)   | ~$0.014  | ~$0.007         |
| Gemini 2.5 Pro (<=200K) | ~$0.011  | ~$0.005         |
| GPT-5.4                 | ~$0.017  | ~$0.009         |
| GPT-4.1                 | ~$0.010  | ~$0.005         |
| GPT-4.1 Nano            | ~$0.001  | ~$0.0003        |
| Mistral Small 4         | ~$0.001  | —               |

#### Self-Hosted OCR (CPU-only, <3 GB RAM)

No self-hosted model within this constraint matches cloud API quality for cursive handwriting. These are the best
available options:

| Model                      | Type                          | RAM          | Handwriting Score   | Full-Page?           | Notes                                                                         |
| -------------------------- | ----------------------------- | ------------ | ------------------- | -------------------- | ----------------------------------------------------------------------------- |
| **PaddleOCR v5**           | Traditional pipeline          | ~500 MB-1 GB | 85%                 | Yes                  | Most practical self-hosted option. Detection + recognition + post-processing. |
| **TrOCR-small**            | Transformer (encoder-decoder) | ~500 MB      | Good (IAM-trained)  | No — line-level only | Requires separate text-line segmentation step.                                |
| **TrOCR-base**             | Transformer (encoder-decoder) | ~1.5 GB      | Better              | No — line-level only | Same limitation, higher quality.                                              |
| **SmolVLM-500M** (GGUF Q4) | Vision-language model         | ~1 GB        | Untested on cursive | Yes                  | End-to-end VLM. OCR quality likely lower than specialised models.             |
| **PaddleOCR-VL 0.9B**      | Vision-language               | ~2-3 GB      | 85% (benchmark)     | Yes                  | Borderline for 3 GB constraint. Leads OmniDocBench.                           |
| Tesseract                  | Rule-based + LSTM             | <200 MB      | ~20-40% on cursive  | Yes                  | Not viable for handwriting. Baseline comparison only.                         |

**Recommendation:** Self-hosted OCR is not yet viable at <3 GB for production cursive handwriting. Use cloud APIs.
Revisit when sub-3B vision-language models improve.

---

### 1b. Voice Transcription

Voice journal entries (1-15 minutes, English) are transcribed to text via speech-to-text models.

**Current:** OpenAI `gpt-4o-transcribe` at $0.006/min.

#### Cloud API Options

| Provider      | Model                    | $/min         | WER   | Max Duration  | Diarisation | Timestamps | Notes                                                          |
| ------------- | ------------------------ | ------------- | ----- | ------------- | ----------- | ---------- | -------------------------------------------------------------- |
| **OpenAI**    | gpt-4o-transcribe        | $0.006        | ~2.5% | 25 min        | No          | Word-level | Current choice.                                                |
| **OpenAI**    | gpt-4o-mini-transcribe   | $0.003        | ~4-5% | 25 min        | No          | Word-level | Half price, lower accuracy.                                    |
| **Mistral**   | Voxtral Mini V2 (batch)  | $0.003        | ~3-4% | 3 hours       | Yes         | Word-level | Context biasing for names. Strong contender.                   |
| **Mistral**   | Voxtral Realtime         | $0.006        | ~1-2% | Streaming     | No          | Word-level | Highest accuracy. Apache 2.0 weights (but needs ~8-10 GB RAM). |
| **Google**    | Cloud STT V2 (Chirp 2)   | $0.016        | ~5-8% | 8 hours       | Yes         | Word-level | Expensive. Dynamic batch at $0.004/min (up to 24h wait).       |
| **Google**    | Gemini 2.5 Flash (audio) | ~$0.002-0.004 | ~3-4% | Context limit | Via prompt  | No native  | Cheap but no native timestamps.                                |
| **Anthropic** | —                        | —             | —     | —             | —           | —          | No transcription API.                                          |
| ElevenLabs    | Scribe v2                | $0.0067       | 2.3%  | —             | Yes         | Word-level | Highest accuracy on benchmarks.                                |
| Deepgram      | Nova-3                   | $0.0043       | ~4-5% | —             | Yes         | Word-level | 400x+ realtime speed.                                          |

#### Self-Hosted Transcription (CPU-only, <3 GB RAM)

| Runtime            | Model               | RAM       | WER   | Speed (CPU)     | Notes                                              |
| ------------------ | ------------------- | --------- | ----- | --------------- | -------------------------------------------------- |
| **whisper.cpp**    | large-v3-turbo Q5_0 | ~1.2 GB   | ~3%   | 1-2x realtime   | Best quality/size ratio. Recommended.              |
| **whisper.cpp**    | large-v3 Q5_0       | ~1.5 GB   | ~2-3% | 0.5-1x realtime | Slightly better accuracy, slower.                  |
| **whisper.cpp**    | small.en            | ~850 MB   | ~4-5% | 3-5x realtime   | Includes tinydiarize for speaker labels.           |
| **whisper.cpp**    | medium.en           | ~2.1 GB   | ~3-4% | 1-2x realtime   | Good middle ground.                                |
| **faster-whisper** | medium (int8)       | ~1.5-2 GB | ~3-4% | ~2x realtime    | Python-native. Built-in VAD. Easiest to integrate. |
| **faster-whisper** | small (int8)        | ~1-1.5 GB | ~4-5% | ~3-4x realtime  | Lighter alternative.                               |
| **Vosk**           | en-us-0.22          | ~2-2.5 GB | 5.7%  | Realtime+       | Purpose-built for offline. Lower accuracy.         |
| **Vosk**           | small-en-us-0.15    | ~300 MB   | 9.9%  | Fast            | Minimal resources but poor accuracy.               |

**Recommendation:** Self-hosted transcription is viable. `whisper.cpp` with `large-v3-turbo-q5_0` (~1.2 GB) gives
near-cloud accuracy for free. `faster-whisper` with int8 medium is the easiest Python integration path.

---

## Stage 2: Enrichment

Plain text is analysed by LLMs to extract structured data. All enrichment tasks use tool_use / structured output to
return typed JSON.

### 2a. Entity Extraction

Extracts named entities (people, places, activities, organisations, topics) and relationships from journal text. Uses
tool_use with a structured schema for reliable JSON output. Confidence-scored. Multi-stage dedup (exact match, alias,
embedding-similarity fallback at cosine >= 0.88).

**Current:** Anthropic Claude Opus 4.6 ($5.00/$25.00).

### 2b. Mood Scoring

Scores journal text on 7 configurable emotional dimensions (e.g., joy<->sadness, anxiety<->eagerness, agency,
energy<->fatigue). Uses tool_use with a dynamically-built schema from `config/mood-dimensions.toml`. Bipolar dimensions
use -1..+1, unipolar use 0..+1.

**Current:** Anthropic Claude Sonnet 4.5 ($3.00/$15.00).

### 2c. Summarisation (Planned)

Generate concise summaries of individual journal entries. Potentially also weekly/monthly digest summaries combining
multiple entries. Needs to capture key events, themes, and emotional tone without hallucinating details.

**Status:** Not yet implemented.

### 2d. Coreference Resolution (Planned)

Resolve pronouns (she, he, they, we) to already-extracted entity names. Runs as a second pass after entity extraction,
using the extracted entities as context. Important for accurate relationship and mention tracking.

**Status:** Tier 3 on roadmap.

### 2e. Predicate Normalisation (Planned)

Map free-text relationship predicates (from entity extraction) to a canonical set. Small classification task — given a
predicate string and the canonical list, return the best match or flag as new.

**Status:** Tier 3 on roadmap. Deferred until predicate drift accumulates.

#### Cloud LLM Options for Enrichment Tasks

All models below support tool_use / structured output (function calling, JSON mode).

**Anthropic**

| Model             | Input $/MTok | Output $/MTok | Context | Batch   | Best For                                                           |
| ----------------- | ------------ | ------------- | ------- | ------- | ------------------------------------------------------------------ |
| Claude Opus 4.6   | $5.00        | $25.00        | 1M      | 50% off | Entity extraction (current), coreference. Frontier quality.        |
| Claude Sonnet 4.6 | $3.00        | $15.00        | 1M      | 50% off | Mood scoring (upgrade from 4.5). Strong all-rounder.               |
| Claude Haiku 4.5  | $1.00        | $5.00         | 200K    | 50% off | Summarisation, predicate normalisation. Near-frontier at low cost. |
| Claude Haiku 3.5  | $0.80        | $4.00         | 200K    | —       | Budget option for simpler tasks.                                   |
| Claude Haiku 3    | $0.25        | $1.25         | 200K    | —       | Cheapest Anthropic. Predicate normalisation only.                  |

**OpenAI**

| Model        | Input $/MTok | Output $/MTok | Context | Batch   | Best For                                                     |
| ------------ | ------------ | ------------- | ------- | ------- | ------------------------------------------------------------ |
| GPT-5.4      | $2.50        | $15.00        | 1.1M    | 50% off | All tasks. Frontier.                                         |
| GPT-5.4 Mini | $0.75        | $4.50         | 400K    | 50% off | Entity extraction, mood scoring. Strong value.               |
| GPT-5.4 Nano | $0.20        | $1.25         | 400K    | 50% off | Entity extraction, summarisation, predicate norm.            |
| GPT-4.1      | $2.00        | $8.00         | 1M      | 50% off | All tasks. Best price/performance for structured extraction. |
| GPT-4.1 Mini | $0.20        | $0.80         | 1M      | 50% off | Summarisation, coreference, predicate norm. Excellent value. |
| GPT-4.1 Nano | $0.05        | $0.20         | 1M      | 50% off | Predicate normalisation. Cheapest major-provider model.      |
| GPT-4o       | $2.50        | $10.00        | 128K    | 50% off | All tasks. Superseded by GPT-4.1 (cheaper, larger context).  |
| GPT-4o Mini  | $0.15        | $0.60         | 128K    | 50% off | Budget all-rounder.                                          |

**Google**

| Model                 | Input $/MTok | Output $/MTok | Context | Batch   | Best For                                                |
| --------------------- | ------------ | ------------- | ------- | ------- | ------------------------------------------------------- |
| Gemini 3.1 Pro        | $2.00        | $12.00        | 1M      | 50% off | All tasks. Leads 13 of 16 major benchmarks.             |
| Gemini 3 Flash        | $0.50        | $3.00         | 1M      | 50% off | All tasks. Free tier (1000 req/day). Outstanding value. |
| Gemini 2.5 Pro        | $1.25        | $10.00        | 1M      | 50% off | All tasks. Previous-gen flagship, still very capable.   |
| Gemini 2.5 Flash      | $0.30        | $2.50         | 1M      | 50% off | Summarisation, predicate norm. Free tier available.     |
| Gemini 2.5 Flash-Lite | $0.10        | $0.40         | 1M      | 50% off | Predicate norm, simple extraction. Free tier.           |

**Mistral**

| Model             | Input $/MTok | Output $/MTok | Context | Best For                                               |
| ----------------- | ------------ | ------------- | ------- | ------------------------------------------------------ |
| Mistral Large 3   | $0.50        | $1.50         | 262K    | All tasks. Very competitive pricing for a large model. |
| Mistral Medium 3  | $0.40        | $2.00         | 131K    | General enrichment.                                    |
| Mistral Small 3.2 | $0.075       | $0.20         | 131K    | Budget extraction. Open-weight (Apache 2.0).           |
| Ministral 3 8B    | $0.15        | $0.15         | 262K    | Predicate norm, simple classification.                 |
| Ministral 3B      | $0.04        | $0.04         | 128K    | Predicate norm only. Limited tool support.             |

#### Task-by-Task Recommendations

**Entity Extraction** (currently Opus 4.6 at $5/$25):

| Priority     | Model                            | Price (in/out)   | Rationale                                                         |
| ------------ | -------------------------------- | ---------------- | ----------------------------------------------------------------- |
| Best quality | Claude Opus 4.6 / Gemini 3.1 Pro | $5/$25 or $2/$12 | Frontier. Gemini is cheaper for comparable quality.               |
| Best value   | GPT-4.1                          | $2/$8            | Strong structured output, 1M context, 60% cheaper than Opus.      |
| Budget API   | GPT-4.1 Mini                     | $0.20/$0.80      | Excellent for batch processing.                                   |
| Self-hosted  | NuExtract 3.8B                   | Free             | Purpose-built for extraction. Matches GPT-4o quality. ~2.3 GB Q4. |

**Mood Scoring** (currently Sonnet 4.5 at $3/$15):

| Priority     | Model             | Price (in/out) | Rationale                                                         |
| ------------ | ----------------- | -------------- | ----------------------------------------------------------------- |
| Best quality | Claude Sonnet 4.6 | $3/$15         | Natural upgrade. Same price, better capabilities.                 |
| Best value   | Gemini 3 Flash    | $0.50/$3.00    | Good nuance at low cost. Free tier for dev.                       |
| Budget API   | Claude Haiku 4.5  | $1/$5          | Near-frontier at Haiku speed.                                     |
| Self-hosted  | Phi-4-mini 3.8B   | Free           | Best small-model reasoning. Native structured output. ~2.3 GB Q4. |

**Summarisation** (planned):

| Priority     | Model            | Price (in/out) | Rationale                                                   |
| ------------ | ---------------- | -------------- | ----------------------------------------------------------- |
| Best value   | GPT-4.1 Mini     | $0.20/$0.80    | Strong summarisation, huge context for multi-entry digests. |
| Budget       | Gemini 2.5 Flash | $0.30/$2.50    | Good quality, free tier for dev.                            |
| Ultra-budget | GPT-4.1 Nano     | $0.05/$0.20    | Cheapest option producing coherent summaries.               |
| Self-hosted  | Phi-4-mini 3.8B  | Free           | Best small-model output quality.                            |

**Coreference Resolution** (planned, Tier 3):

| Priority   | Model            | Price (in/out) | Rationale                                                        |
| ---------- | ---------------- | -------------- | ---------------------------------------------------------------- |
| Best value | GPT-4.1 Mini     | $0.20/$0.80    | Good contextual understanding. Can batch with entity extraction. |
| Budget     | Claude Haiku 4.5 | $1/$5          | Strong context tracking.                                         |

**Predicate Normalisation** (planned, Tier 3):

| Priority    | Model                    | Price (in/out) | Rationale                                        |
| ----------- | ------------------------ | -------------- | ------------------------------------------------ |
| Best value  | GPT-4.1 Nano             | $0.05/$0.20    | Simple classification task. Cheapest option.     |
| Budget      | Gemini 2.5 Flash-Lite    | $0.10/$0.40    | Free tier.                                       |
| Self-hosted | Any 1-3B model + grammar | Free           | Classification is within small-model capability. |

#### Self-Hosted LLMs for Enrichment (CPU-only, <3 GB RAM)

All models below use Q4_K_M quantisation and run via llama.cpp or Ollama.

| Model              | Params | Q4 Size | Tool Calling                  | Best For                                       | Notes                                             |
| ------------------ | ------ | ------- | ----------------------------- | ---------------------------------------------- | ------------------------------------------------- |
| **Phi-4-mini**     | 3.8B   | ~2.3 GB | Native                        | Mood scoring, summarisation, coreference       | Strongest reasoning in class. Tight fit for 3 GB. |
| **Llama 3.2 3B**   | 3B     | ~1.8 GB | Native (BFCL V2: 67%)         | Entity extraction, general enrichment          | Best tool-calling sub-3B.                         |
| **NuExtract 3.8B** | 3.8B   | ~2.3 GB | Template-based (not tool_use) | Entity extraction specifically                 | Purpose-built. Matches GPT-4o extraction quality. |
| **Qwen3 1.7B**     | 1.7B   | ~1.1 GB | Via grammar constraint        | Predicate normalisation, simple classification | Dual-mode (thinking/non-thinking).                |
| **Gemma 3 1B**     | 1B     | ~0.7 GB | Via grammar constraint        | Predicate normalisation                        | 128K context. Tiny footprint.                     |
| **SmolLM2 1.7B**   | 1.7B   | ~1.1 GB | Via grammar constraint        | Simple classification                          | Lightweight.                                      |
| **TinyLlama 1.1B** | 1.1B   | ~0.7 GB | Via grammar constraint        | Fastest inference                              | ~10-15 tok/s on CPU. Simplest tasks only.         |

**Structured output for local models:** Ollama's `format` parameter and llama.cpp's GBNF grammars enable
grammar-constrained decoding for any model, guaranteeing valid JSON output regardless of whether the model was
specifically trained for tool calling.

**CPU inference speed:** 3-4B models run at ~3-8 tok/s on modern x86 CPUs. Acceptable for batch ingestion pipeline; not
suitable for interactive use.

---

## Stage 3: Embedding & Chunking

Text chunks are embedded into vectors for semantic search and similarity operations.

### Current Setup

OpenAI `text-embedding-3-large` at 1024 dimensions (reduced from native 3072 via Matryoshka support). Used for:

- Semantic chunking (sentence-level embedding, cosine similarity to find topic boundaries)
- Semantic search (query embedding -> ChromaDB cosine search)
- Entity name deduplication (cosine similarity >= 0.88 threshold)

#### Cloud API Options

| Provider    | Model                      | Dims (native / configurable) | $/MTok | Max Input  | MTEB Avg | STS   | Matryoshka | Notes                                                      |
| ----------- | -------------------------- | ---------------------------- | ------ | ---------- | -------- | ----- | ---------- | ---------------------------------------------------------- |
| **OpenAI**  | text-embedding-3-large     | 3072 / 256-3072              | $0.13  | 8,191      | 64.6     | 83.2  | Yes        | Current. Batch: $0.065.                                    |
| **OpenAI**  | text-embedding-3-small     | 1536 / 512-1536              | $0.02  | 8,191      | 62.3     | 80.5  | Yes        | 6.5x cheaper, ~2 points lower.                             |
| **Google**  | gemini-embedding-001       | 3072 / 128-3072              | $0.15  | 2,048      | 68.3     | —     | Yes        | Best MTEB score. 2K token limit fine for 150-token chunks. |
| **Google**  | text-embedding-005         | 768 (fixed)                  | $0.006 | 2,048      | 63.8     | 83.0  | No         | 21x cheaper than current. ~1 point lower.                  |
| **Google**  | gemini-embedding-2-preview | 3072 / 128-3072              | $0.20  | 8,192      | —        | —     | Yes        | Multimodal (text/image/audio/video).                       |
| **Voyage**  | voyage-4-large             | 1024 / 256-2048              | $0.12  | 32,000     | ~67+     | —     | Flexible   | Anthropic's recommended partner. int8/binary quantisation. |
| **Voyage**  | voyage-4                   | 1024 / 256-2048              | $0.06  | 32,000     | —        | —     | Flexible   | 200M free tokens.                                          |
| **Voyage**  | voyage-3-large             | 1024 / 256-2048              | $0.18  | 32,000     | 65.1     | 84.5  | Flexible   | Best STS score — ideal for entity dedup.                   |
| **Mistral** | mistral-embed              | 1024 (fixed)                 | $0.10  | 8,192      | ~61      | —     | No         | Aging. Lower MTEB than competitors.                        |
| **Mistral** | codestral-embed            | up to 3072 (flexible)        | $0.15  | 8,192      | —        | —     | Yes        | Code-focused. Overkill for journal text.                   |
| **Cohere**  | embed-v4                   | 1024                         | $0.10  | 4,096-128K | 64-65    | 84-85 | —          | Competitive STS.                                           |

**Note:** Anthropic does not offer an embedding model. They recommend Voyage AI.

#### Self-Hosted Embedding Models (CPU-only, <3 GB RAM)

| Model                     | Params             | Dims              | Max Tokens | Size (Q4 / FP16)    | MTEB Avg | STS  | Matryoshka | Notes                                                    |
| ------------------------- | ------------------ | ----------------- | ---------- | ------------------- | -------- | ---- | ---------- | -------------------------------------------------------- |
| **nomic-embed-text-v1.5** | 137M               | 768 (flex to 64)  | 8,192      | 81 MB (Q4) / 262 MB | ~62.4    | —    | Yes        | Best self-hosted option. Runs via llama.cpp. Apache 2.0. |
| **bge-small-en-v1.5**     | 33M                | 384               | 512        | ~67 MB (FP16)       | 62.2     | 81.6 | No         | Tiny footprint. Best STS in small tier. MIT.             |
| **bge-base-en-v1.5**      | 109M               | 768               | 512        | ~440 MB (FP32)      | ~63.5    | —    | No         | Good middle ground. MIT.                                 |
| nomic-embed-text-v2-moe   | 475M (305M active) | 768 (flex to 256) | 512        | 328 MB (Q4)         | ~63+     | —    | Yes        | MoE. 512 token limit is restrictive. Apache 2.0.         |
| all-MiniLM-L6-v2          | 22.7M              | 384               | 256        | ~45 MB (FP16)       | 56.3     | ~78  | No         | 256-token max is tight. Lower quality.                   |
| snowflake-arctic-embed-s  | 33M                | 384               | 512        | ~67 MB (FP16)       | ~52      | —    | No         | Lower quality than BGE.                                  |

**Recommendation:** `nomic-embed-text-v1.5` at 81 MB Q4 is the clear winner for self-hosting. Quality is comparable to
`text-embedding-3-small`, it supports Matryoshka for flexible dimensions, has 8K context, and runs on any CPU via
llama.cpp. Zero ongoing API cost.

---

## Stage 4: Query & Search

User queries are processed to find relevant journal entries.

| Method                  | How It Works                                                | LLM Involved?                          |
| ----------------------- | ----------------------------------------------------------- | -------------------------------------- |
| **Semantic search**     | Query text -> embed via OpenAI -> cosine search in ChromaDB | Embedding model only (same as Stage 3) |
| **Keyword search**      | FTS5 full-text index on SQLite                              | No LLM                                 |
| **Stats / aggregation** | SQL queries on structured data                              | No LLM                                 |

No additional model choices needed for this stage — it reuses the embedding model from Stage 3.

**Future consideration:** A query-response synthesis step could use an LLM to generate natural-language answers from
retrieved chunks, rather than just returning raw results. This would use the same models as summarisation (Stage 2c).

---

## Infrastructure Services

### ChromaDB (Vector Store)

- **Role:** Stores chunk embeddings, performs cosine-distance nearest-neighbour search (HNSW algorithm)
- **SDK:** `chromadb-client>=1.5,<2`
- **Connection:** HTTP client to `CHROMADB_HOST:CHROMADB_PORT` (default localhost:8000)
- **Collection:** `journal_entries` with `hnsw:space=cosine`
- **Deployment:** Docker container alongside journal-server (compose-internal networking)

### SQLite + FTS5

- **Role:** Structured storage (entries, entities, relationships, mood scores, jobs), keyword search via FTS5
- **Connection:** Local file (`journal.db`), WAL mode
- **Migrations:** PRAGMA user_version based, SQL files in `db/migrations/`

### tiktoken

- **Role:** Token counting for chunk overlay visualisation in the webapp
- **Encoding:** `cl100k_base` (matches `text-embedding-3-large`)
- **Note:** If you switch embedding models, verify the tokeniser still matches or update accordingly.

---

## Cost Optimisation Strategies

### Batch APIs

Both Anthropic and OpenAI offer 50% discounts on batch/async API calls. Journal ingestion (OCR, entity extraction, mood
scoring, embedding) is not latency-sensitive — batch processing is ideal for:

- Bulk ingestion of historical pages
- Periodic re-extraction or mood backfills
- Any enrichment step triggered asynchronously via the job runner

### Prompt Caching

Anthropic's prompt caching reduces input token costs to 0.1x for cache hits. The journal-server already uses this for the
OCR context-priming glossary (system prompt). The entity extraction system prompt and mood scoring system prompt are also
cacheable — both use `cache_control: ephemeral`.

### Free Tiers

Google Gemini offers free tiers on several models (rate-limited):

- Gemini 2.5 Pro, 2.5 Flash, 2.5 Flash-Lite, 3 Flash: free tier available
- Embedding models: up to 1,000 daily requests free

Voyage AI offers 200M free tokens for v4 embedding models.

These free tiers are useful for development, testing, and low-volume personal use.

### Right-Sizing Models to Tasks

Not every task needs a frontier model:

| Task Complexity | Examples                                       | Appropriate Tier            | Typical Cost                      |
| --------------- | ---------------------------------------------- | --------------------------- | --------------------------------- |
| High            | OCR (cursive), entity extraction, mood scoring | Frontier / near-frontier    | $1-5 / MTok input                 |
| Medium          | Summarisation, coreference resolution          | Mid-tier                    | $0.20-1.00 / MTok input           |
| Low             | Predicate normalisation, simple classification | Budget / nano / self-hosted | $0.05-0.10 / MTok input (or free) |

---

## Provider SDK Versions

Current dependencies from `pyproject.toml`:

```
anthropic>=0.87,<1          # OCR, entity extraction, mood scoring
google-genai>=1,<2          # Alternative OCR provider
openai>=2.29,<3             # Transcription, embeddings
chromadb-client>=1.5,<2     # Vector store
mcp[cli]>=1.26,<2           # MCP server framework
tiktoken>=0.9,<1            # Token counting (cl100k_base)
```

---

## Processing Pipeline: Single Entry Lifecycle

```
  IMAGE / VOICE / TEXT
         │
         ▼
  ┌──────────────────────────────────────────────────────┐
  │  1. INPUT → TEXT                        (request)    │
  │                                                      │
  │  Image ──► OCR ──────────────► plain text            │
  │             ╰─ 1 LLM call (Anthropic vision)        │
  │                                                      │
  │  Voice ──► Transcribe ───────► plain text            │
  │             ╰─ 1 API call (OpenAI Whisper)          │
  │                                                      │
  │  Text ───► (pass-through) ──► plain text             │
  └──────────────────────┬───────────────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────────────┐
  │  2. CHUNK + EMBED                       (request)    │
  │                                                      │
  │  Sentences ──► embed all ──► cosine similarity       │
  │                ╰─ 1 embedding call (OpenAI)         │
  │                                                      │
  │  Chunks ──► embed all ──► store in ChromaDB          │
  │              ╰─ 1 embedding call (OpenAI)           │
  └──────────────────────┬───────────────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────────────┐
  │  3. ENRICH                          (background)     │
  │                                                      │
  │  Entity extraction ──► entities + relationships      │
  │   ╰─ 1 LLM call (Anthropic, tool_use)              │
  │   ╰─ 0-1 embedding call (dedup, if new entities)   │
  │                                                      │
  │  Mood scoring ──► 7 dimension scores  (if enabled)   │
  │   ╰─ 1 LLM call (Anthropic, tool_use)              │
  │                                                      │
  │  Summarisation ──► entry summary      (planned)      │
  │  Coreference ──► resolved pronouns    (planned)      │
  │  Predicate norm ──► canonical preds   (planned)      │
  └──────────────────────────────────────────────────────┘

  ─────────────── LATER, ON QUERY ───────────────

  ┌──────────────────────────────────────────────────────┐
  │  4. SEARCH                              (on demand)  │
  │                                                      │
  │  Semantic ──► embed query ──► ChromaDB cosine        │
  │               ╰─ 1 embedding call (OpenAI)          │
  │                                                      │
  │  Keyword ──► FTS5 (no API call)                      │
  │  Stats ──► SQL aggregation (no API call)             │
  │  Synthesis ──► LLM answer generation    (planned)    │
  └──────────────────────────────────────────────────────┘
```
