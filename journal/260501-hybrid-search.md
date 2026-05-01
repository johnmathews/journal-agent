# 2026-05-01 — Hybrid search

Replaced the `mode=keyword|semantic` toggle on `/api/search` and the
semantic-only `journal_search_entries` MCP tool with a single hybrid
pipeline. Every search call now runs BM25 (FTS5) + dense (Chroma)
retrieval in parallel, fuses the rankings with Reciprocal Rank Fusion
(`k=60`), and reranks the fused top-30 with a listwise Claude Haiku
call.

## Architecture

L1: parallel retrieval. BM25 returns top-50 entries (entry-level,
default FTS5 BM25, `entries_fts` indexes whole-entry `raw_text`).
Dense returns top-50 chunks via Chroma cosine, then groups to
entries (best chunk per entry as the ranking signal, retain all
matching chunks for display).

Fusion: `rrf_fuse({"bm25": [...], "dense": [...]}, k=60)` →
top-30 by RRF score.

L2: rerank. `Reranker` Protocol with three adapters:
`AnthropicReranker` (default, `claude-haiku-4-5`, listwise JSON),
`NoopReranker` (passes RRF order through), and `build_reranker()`
factory selecting via `HYBRID_RERANKER` env var. Anthropic adapter
falls back to noop on any error so search never 500s on the L2
stage.

## Granularity decision: entry-level fusion

Considered chunk-level fusion (would need a new `chunks_fts` virtual
table + sync triggers). Decided against because chunks are ~150
tokens (`CHUNKING_MAX_TOKENS`) — too short for BM25 IDF stats to
mean much — and the UI contract is already entry-with-matching-
chunks. Documented in `docs/search.md` as a non-breaking follow-up
if eval data ever shows it matters.

## Reranker decision: Claude Haiku first

Considered Voyage `rerank-2.5` (faster, cheaper, dedicated cross-
encoder). Picked Haiku for v1 because the project already owns the
Anthropic SDK — zero new deps, zero new API key. Voyage adapter is a
~30-line follow-up when latency matters. Research notes captured in
`.engineering-team/plan-hybrid-search.md`.

## API contract changes

`/api/search`:
- `mode` parameter is a hard `400 mode_removed` (was `400 invalid_mode`).
- Response envelope drops `mode`, adds `reranker` (class name of the
  active L2 stage — useful for debugging and webapp cache busting).
- Items now carry **both** `snippet` (when BM25 contributed) and
  `matching_chunks` (when dense contributed). Either or both may be
  present.

`journal_search_entries` MCP tool:
- Same call signature; new docstring explicitly mentions hybrid and
  cross-references `journal_get_entries_by_date` /
  `journal_list_entries` so an LLM consumer doesn't pick `search`
  for date-only browsing.

`/api/settings` adds a `search` block surfacing the pipeline knobs.

## Configuration

Six new env vars, all with defaults aligned with published guidance:

| Env var | Default | What |
|---|---|---|
| `HYBRID_BM25_CANDIDATES` | 50 | Top-N from FTS5 |
| `HYBRID_DENSE_CANDIDATES` | 50 | Top-N from Chroma |
| `HYBRID_FUSION_TOP_M` | 30 | Kept after RRF |
| `HYBRID_RRF_K` | 60 | Cormack et al. default |
| `HYBRID_RERANKER` | `anthropic` | `anthropic` \| `none` |
| `RERANKER_MODEL` | `claude-haiku-4-5` | Model for AnthropicReranker |

## Code map

New:
- `src/journal/providers/reranker.py` — Protocol + dataclasses +
  Noop + Anthropic + factory.
- `src/journal/services/hybrid.py` — `rrf_fuse`, `HybridConfig`,
  `HybridSearchService`.
- `tests/test_providers/test_reranker.py` (23 tests).
- `tests/test_services/test_hybrid.py` (20 tests including two
  corpus-quirk tests: proper-noun-only-via-BM25 and
  paraphrase-only-via-dense).
- `docs/search.md`.

Modified:
- `src/journal/config.py` — six new fields.
- `src/journal/services/query.py` — rewritten to delegate to
  `HybridSearchService`. `keyword_search` retired; `_repo` and
  `_vector_store` kept as attributes for `/health` compatibility.
- `src/journal/api.py` — `/api/search` route, `/api/settings`
  search block.
- `src/journal/mcp_server.py` — wire reranker into lifespan, update
  `journal_search_entries` docstring.
- `tests/test_api.py`, `tests/test_services/test_query.py`,
  `docs/configuration.md`.

## Outcomes

- 1449 server tests passing (43 net new).
- Two webapp commits (server + webapp ship in lockstep). Webapp
  drops the toggle, renders mixed signal explanation
  ("Matched by keywords", "by meaning", "by keywords and meaning").
- Coverage: server suite green; webapp branches at 85.03% (just
  above the 85% pre-push threshold).

## Follow-ups for the eval set

The plan flagged an eval harness (~30–50 query/expected-entry pairs
in `tests/eval/queries.jsonl`) as the natural next step — it
unblocks every later tuning decision (weighted RRF, Voyage adapter
swap, chunk-level FTS5). Deferred for speed; worth doing before any
tuning work to avoid flying blind.
