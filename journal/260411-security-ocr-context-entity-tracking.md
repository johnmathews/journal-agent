# 2026-04-11 — Security hardening, OCR context priming, entity tracking

Big cross-cutting session. Three threads of work landed on the same
branch (`eng-session-2026-04-10`) because the server and webapp had
to move together on the auth change, and the entity feature touches
most of the codebase at once.

## What shipped

### Security — the biggest and most urgent thread

The engineering-team research pass flagged that the REST API and MCP
streamable-HTTP endpoint were wide open: no auth, no TLS, binds on
`0.0.0.0`, DNS rebinding protection explicitly disabled in code.
Closing all of it in one branch:

- `src/journal/auth.py` — new `BearerTokenMiddleware` (constant-time
  token comparison via `hmac.compare_digest`). Allows `OPTIONS`
  through so CORS preflights still work; rejects every other method
  without `Authorization: Bearer <token>`.
- `src/journal/mcp_server.py` — fail-closed startup check: refuses
  to start if `JOURNAL_API_TOKEN` is unset. DNS rebinding protection
  is always on; removed the `else` branch that turned it off when
  `MCP_ALLOWED_HOSTS` was empty. `config.mcp_allowed_hosts` now
  defaults to `["127.0.0.1", "localhost"]` so there is no "zero
  allowed hosts" path. Middleware stack order: CORS outside,
  BearerToken inside, so 401 responses still carry CORS headers
  (browsers swallow CORS-less 401s as opaque errors).
- `docker-compose.yml` — host-side port bind is now `127.0.0.1:8400`,
  not `0.0.0.0:8400`. ChromaDB's `ports:` block removed entirely
  (it's only reachable via the internal compose network). Added the
  `JOURNAL_API_TOKEN` and `MCP_ALLOWED_HOSTS` env vars.
- `src/journal/services/ingestion.py` — new `_validate_public_url`
  SSRF guard. Called before any socket is opened in `_download`.
  Refuses non-HTTP(S) schemes, resolves hostname via DNS, and rejects
  loopback / RFC1918 / link-local (cloud metadata `169.254.169.254`)
  / multicast / reserved / unspecified addresses. Does not defend
  against DNS rebinding between resolution and connect — that's a
  socket-layer fix out of scope for a personal tool. Real threat
  surface (loopback and RFC1918) is closed.
- `docs/security.md` — new doc with the threat model, defences,
  provider retention notes, and "what is NOT protected" section.
- `chmod 600` applied to `journal.db` and `.local-journal.db` in
  the main checkout (L6 finding).

### OCR context priming

Optional feature: a directory of markdown files loaded once at
startup and injected into the OCR system prompt so Claude knows
family names, places, and topics. Feature is opt-in via
`OCR_CONTEXT_DIR` — when unset, the adapter behaves identically to
the pre-feature version.

- `src/journal/providers/ocr.py` — new `load_context_files()` that
  reads markdown files from a directory in alphabetical order and
  concatenates them with H1 headers derived from filename stems.
  `AnthropicOCRProvider.__init__` now takes `context_dir` and
  `cache_ttl` ("5m" or "1h") parameters. At init time it composes
  the system text, counts tokens with tiktoken (cl100k_base as a
  proxy for Claude's tokenizer), and logs a loud WARNING if the
  composed block is below the **4,096-token cache minimum** — below
  which Anthropic silently ignores `cache_control` and bills every
  request at full input rate. A prepended `CONTEXT_USAGE_INSTRUCTIONS`
  block tells Claude to use glossary entries only when pen strokes
  are visually consistent, mitigating the hallucinated-substitution
  failure mode.
- `context/README.md` + gitignore exclusion: the directory itself
  is checked in but its contents are local-only (personal names and
  places should never be committed). Users drop their own
  `people.md`, `places.md`, `topics.md` files there.
- `docs/ocr-context.md` — design doc with mechanism, cost model,
  risks, and the token-minimum gotcha.

### Entity tracking — backend

New feature, backend scaffolding. On-demand batch job extracts
people, places, activities, organisations, topics, and relationships
between them from entry text using Claude tool use.

- `src/journal/db/migrations/0004_entities.sql` — four new tables
  (`entities`, `entity_aliases`, `entity_mentions`,
  `entity_relationships`) plus an `entity_extraction_stale` column
  on `entries` with a trigger that flips it to 1 on any `final_text`
  UPDATE. The extraction service clears the flag when it finishes.
- `src/journal/entitystore/store.py` — new package with `EntityStore`
  Protocol + `SQLiteEntityStore` implementation. The Protocol is the
  key architectural decision: it means a future graph-DB backend
  (LadybugDB, likely) can be swapped in without touching the service
  layer. SQLite is the source of truth; any graph store is treated
  as a derived, rebuildable materialized view. This pattern came
  directly out of the Phase 1 research.
- `src/journal/providers/extraction.py` — `ExtractionProvider`
  Protocol + `AnthropicExtractionProvider`. Uses Claude tool use
  with a forced `tool_choice` so the model returns structured JSON.
  The system prompt enumerates entity types, lists preferred
  predicates, and inlines the author's name so first-person
  statements ("I played squash") become
  (`<author>`, `plays`, `squash`) triples.
- `src/journal/services/entity_extraction.py` —
  `EntityExtractionService` with staged dedup:
  1. Exact canonical-name match (case-folded)
  2. Alias match against `entity_aliases`
  3. Embedding-similarity fallback (cosine ≥ 0.88, configurable)
     via the existing `EmbeddingsProvider`. Stage c produces a
     warning rather than auto-merging so the user can review.
  
  Extraction is idempotent: rerunning for the same entry deletes
  prior mentions and relationships before writing new ones. Per-entry
  exceptions in `extract_batch` are caught and surfaced as warnings
  so a single bad entry can't halt the batch.
- `src/journal/api.py` — six new REST endpoints under `/api/entities`
  plus `GET /api/entries/{id}/entities` for the webapp's chip strip.
- `src/journal/mcp_server.py` — four new MCP tools:
  `journal_extract_entities`, `journal_list_entities`,
  `journal_get_entity_mentions`, `journal_get_entity_relationships`.
- `src/journal/cli.py` — new `journal extract-entities` subcommand
  with `--entry-id`, `--start-date`, `--end-date`, `--stale-only`
  filters.
- `src/journal/config.py` — added `entity_extraction_model`,
  `entity_extraction_max_tokens`, `entity_dedup_similarity_threshold`,
  `journal_author_name` (default "John") fields.
- `docs/entity-tracking.md` — new doc covering storage architecture,
  dedup strategy, query surface, and known risks (predicate drift,
  entity drift, cost at scale).

## Test count

- Baseline before session: 272 passing
- After session: **366 passing** (94 new tests)

Breakdown of new tests:
- `tests/test_auth.py` — 9 tests for the bearer-token middleware
- `tests/test_services/test_ssrf.py` — 14 tests for `_validate_public_url`
- `tests/test_providers/test_ocr.py` — 14 new tests for context file
  loading, cache TTL, and cache minimum warning
- `tests/test_providers/test_extraction.py` — tests for the extraction
  provider's tool-use API and response parsing
- `tests/test_services/test_entity_store.py` — tests for the SQLite
  entity store (create, alias, embedding, list, mention, relationship)
- `tests/test_services/test_entity_extraction.py` — tests for the
  extraction service including idempotency, staged dedup, batch
  failure handling, author pronoun resolution
- Plus extensions to `test_config.py` for the new config fields

All tests pass, ruff clean.

## Architectural decisions worth remembering

1. **Security is defense in depth**: bearer token + loopback binding
   + DNS rebinding protection + SSRF guard. Each layer is independently
   sufficient to close its own attack class; none of them replaces
   another. The compose file bind + middleware combo means an attacker
   needs both (a) network access to `127.0.0.1:8400` on the host (or
   the reverse proxy), AND (b) the bearer token.

2. **SQLite remains the source of truth** for entity tracking, even
   though the user explicitly wants to experiment with graph DBs. The
   graph DB, when it's added, will be a materialized view rebuildable
   from SQLite with one command. This pattern was the direct output
   of the Phase 1 research and it's the right call — it means
   experimenting with LadybugDB is a zero-architectural-risk bet.

3. **Predicates are free-text**, not enums. The extraction service
   stores whatever Claude emits. Normalisation is a future pass.

4. **Extraction is idempotent by design**: every re-run deletes prior
   mentions and relationships for the entry before writing fresh ones.
   This is how we handle the "user edited an entry, re-extract" path
   without accumulating duplicates. The `entity_extraction_stale`
   column + trigger gives a filter for "only re-extract changed
   entries".

5. **Fail-closed on missing auth token.** The server refuses to start
   without `JOURNAL_API_TOKEN`. No "auth off" mode. The webapp client
   (journal-webapp) now sends the header; both must be configured
   together.

## Deferred to a future session

- **LadybugDB / graph DB experiment** — swap in a second
  `EntityStore` implementation backed by the Kuzu-successor project.
  Defer until we have extracted entities to play with.
- **Graph visualization view** on the webapp — Cytoscape.js won the
  library bake-off but deferred to Phase 2. No point visualising an
  empty graph.
- **Provider Zero Data Retention agreements** with Anthropic and
  OpenAI. Policy decision, not code.
- **TLS / reverse proxy** setup at the VM level.
- **Encrypted backup** script for `/srv/media/config/journal`.
- **Predicate normalisation pass** for the entity graph. Free-text
  predicates will drift — eventually worth clustering them.
- **Coreference resolution** for pronouns beyond the author's "I"
  (we, she, him). Currently only first-person self-reference is
  handled.

## Context for the next session

The entity extraction has not been run on any real entries yet — the
plumbing works end-to-end in tests but no real Anthropic calls have
happened. First real run should:
1. Pick a single entry you know well via `journal extract-entities --entry-id N`
2. Spot-check the output against what you'd expect
3. Tune the dedup threshold if needed
4. Only then do a full batch run

Second-session checklist: evaluate whether the OCR context priming
actually improves proper-noun accuracy. Run a sample with and without
the feature enabled and eyeball the results.
