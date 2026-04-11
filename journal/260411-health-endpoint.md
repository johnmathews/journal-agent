# 2026-04-11 — `/health` endpoint (Tier 1 item 2)

Session 3 of the Tier 1 plan. Backend-only in `journal-server`.
Closes work units **T1.2.a** through **T1.2.f** from
`docs/tier-1-plan.md`. Builds the pieces the future dashboard
(Tier 1 item 3) would want — in-process query latency, ingestion
aggregates, per-component liveness — and surfaces them at an
unauthenticated `GET /health`.

## What shipped

### 1. `InMemoryStatsCollector` (`src/journal/services/stats.py`)

New module with the `StatsCollector` protocol and an in-process
implementation:

- `record_query(query_type, latency_ms)` pushes a sample into a
  per-type `deque(maxlen=1000)` ring buffer and bumps an exact
  counter. Negative latencies are clamped to zero (a monotonic
  clock hiccup shouldn't crash the server).
- `snapshot()` returns a `StatsSnapshot` carrying `total_queries`,
  per-type `QueryTypeStats` with nearest-rank p50/p95/p99,
  `uptime_seconds`, and `started_at` ISO-8601 timestamp.
- Everything is guarded by a single `threading.Lock`. FastMCP is
  usually asyncio single-threaded but some adapter paths run via
  `run_in_executor`, and the lock is microseconds of overhead.
- Percentiles use the **nearest-rank** formula against a sorted
  copy of the buffer. For `n < 20` the p99 degenerates to the max,
  which is the right thing at low throughput.

10 unit tests in `tests/test_services/test_stats.py` cover:
empty state, single sample, multi-type tracking, negative-latency
clamp, percentile correctness on 100 samples, bounded memory
(counter stays exact past the buffer cap), concurrent-write
safety, uptime monotonicity.

### 2. `QueryService` stats wiring

`QueryService.__init__` gained an optional
`stats: StatsCollector | None = None` parameter. A new
`_timed(query_type, fn)` helper runs `fn()` and records its
latency when `stats` is set, otherwise it's a straight passthrough
with no clock reads.

The big methods (`search_entries`, `keyword_search`) now have
tiny public wrappers that call `_timed` with an inner
`_search_entries_impl` / `_keyword_search_impl` that holds the
actual body. The one-line methods (`get_statistics`,
`get_mood_trends`, `get_topic_frequency`) call `_timed` inline
with a lambda delegating to the repo. Zero-overhead when
`stats=None`: no allocations, no locks, no clock reads.

4 integration tests in `test_query.py::TestStatsCollectorIntegration`
assert that each method type records under the expected name and
that the `None` passthrough still works.

### 3. `get_ingestion_stats` repository method

New `SQLiteEntryRepository.get_ingestion_stats(now)` returning an
`IngestionStats` dataclass (new, in `models.py`). Fields:

- `total_entries`
- `entries_last_7d`, `entries_last_30d` (computed from the
  injected `now` param so tests can drive the clock)
- `by_source_type` — `dict[str, int]` from `GROUP BY source_type`
- `avg_words_per_entry`, `avg_chunks_per_entry` (rounded to 2 dp)
- `last_ingestion_at` — `MAX(created_at)`
- `total_chunks` — `SUM(chunk_count)`
- `row_counts` — fixed-list row counts from
  `_HEALTH_ROW_COUNT_TABLES`: entries, entry_pages, entry_chunks,
  mood_scores, source_files, entities, entity_aliases,
  entity_mentions, entity_relationships. Hardcoded rather than
  enumerated via `sqlite_master` so the `/health` contract is
  stable across schema additions and doesn't accidentally surface
  FTS5 shadow tables.

4 unit tests: empty corpus, counts by source and date windows,
`update_chunk_count` reflected in `avg_chunks_per_entry`, entity
tables present in `row_counts` without any extraction having run.

### 4. Liveness checks (`src/journal/services/liveness.py`)

New module with four functions and an overall-status rollup:

- `check_sqlite(conn)` — `SELECT 1`. Errors on a closed connection.
- `check_chromadb(store)` — calls `store.count()`. Duck-typed so
  tests can pass a `MagicMock` or `InMemoryVectorStore`.
- `check_api_key(name, key, min_length=20)` — static check. A
  missing or short key returns `degraded` (not `error`) because
  the *service* is up; this is an operator config issue, not a
  broken component.
- `overall_status([checks])` — worst-of rollup: any `error` →
  `error`, else any `degraded` → `degraded`, else `ok`. An empty
  list returns `ok` so `/health` doesn't crash in an impossible-
  in-practice edge case.

12 unit tests cover each check and the rollup.

### 5. `GET /health` route (`src/journal/api.py`)

Returns a four-block envelope:

1. `status` — the rollup from `overall_status`.
2. `checks` — list of per-component `ComponentCheck` dicts
   (`name`, `status`, `detail`, `error`).
3. `ingestion` — the full `IngestionStats` as a dict.
4. `queries` — `total_queries`, `uptime_seconds`, `started_at`,
   and `by_type` with per-type count + latency percentiles.

**Unauthenticated.** `BearerTokenMiddleware` gained an
`exempt_paths: set[str] | None = None` kwarg; `mcp_server.main()`
passes `{"/health"}`. Matching is exact-path (not prefix) to
prevent `/health/private` or similar from leaking through.
Justification:

- Server binds to `127.0.0.1` per the 2026-04-11 security
  hardening. Anyone who can reach `/health` already has a shell
  on the box.
- The payload is **scrubbed of query content** — `queries.by_type`
  carries counts and latency only, never query strings. A test
  (`test_health_payload_never_includes_search_terms`) serializes
  the response back to JSON and asserts the search term from a
  prior `keyword_search` call does not appear anywhere in the
  envelope.
- If the server ever gets fronted by a reverse proxy for external
  access, `/health` must be excluded from the public route or the
  `queries.by_type` block scrubbed. Documented in `docs/api.md`.

6 integration tests in `test_api.py::TestHealth`: empty server,
populated corpus, query stats after real searches, degraded on
missing API keys, 503 on uninitialized services, and the privacy
guard test.

### 6. `BearerTokenMiddleware.exempt_paths`

Small additive change to `src/journal/auth.py`. `__init__`
accepts an optional `exempt_paths: set[str] | None = None`;
`dispatch()` checks `request.url.path in self._exempt_paths`
before the bearer check. `request.url.path` excludes the query
string, so callers can't sneak a match with `?foo=bar`.

5 new tests in `test_auth.py::TestExemptPaths`: exempt path
allowed without token, non-exempt still requires auth, exact
match (not prefix), default empty still protects `/health`,
query-string ignored.

### 7. `journal health` CLI

New subcommand in `cli.py` (`cmd_health`). Builds services
locally (fresh SQLite connection, fresh ChromaDB client), runs
the ingestion stats query and all liveness checks, and prints
the same JSON envelope as the HTTP endpoint. Flags:

- `--compact` — single-line JSON suitable for piping to `jq`.
  Without it, output is indented + sorted.

Exits `0` on `ok` / `degraded`, `2` on `error`. Docker
healthchecks or cron jobs can probe the CLI without a running
server by piping the output.

3 new tests in `test_cli.py`: help output mentions `--compact`,
`cmd_health` emits valid JSON with all four check components
(ChromaDB is `patch`-ed out so no running container is needed),
and `--compact` mode is a single line.

## Deliberate non-goals for this session

1. **Most-frequent search terms.** The plan's optional "Query &
   usage stat" #4. Would leak what the user was searching for
   and require tracking raw query strings in memory from an
   unauthenticated endpoint. Skipped — query stats block is
   counts-only. Documented in `docs/api.md` and the roadmap.
2. **ChromaDB last-update timestamp.** Chroma doesn't expose a
   cheap "last write" probe; getting one would require storing
   it in metadata on every write. Not worth it — `total_chunks`
   in `row_counts` is a reasonable proxy for corpus growth.
3. **SQLite database size in bytes.** Requires a separate
   `os.stat()` call and doesn't generalise to non-file DBs.
   `row_counts` is the better shape.
4. **Prometheus text format.** JSON only. Adding a second
   content-type is a one-day job when an actual Prometheus
   consumer appears.
5. **Dashboard endpoint reuse.** The plan noted that item 3's
   dashboard could read from `/health`. Not pursued — when 3a
   lands it'll add its own aggregation endpoints. The shared-
   source idea can be revisited if it turns out to duplicate work.
6. **Deeper credential probes.** `check_api_key` is a static
   length check. Making a real Anthropic/OpenAI call on every
   `/health` hit would cost money and rate limits; the static
   check is good enough to catch "env var is unset" and "env
   var is a placeholder like `xxx`".

## Tests and quality gates

- **Before:** 392 tests passing.
- **After:** 436 tests passing. +44 new tests across 6 files:
  1. `tests/test_services/test_stats.py` — 10 new tests
  2. `tests/test_services/test_liveness.py` — 12 new tests
  3. `tests/test_db/test_repository.py::TestIngestionStats` — 4
  4. `tests/test_services/test_query.py::TestStatsCollectorIntegration` — 4
  5. `tests/test_auth.py::TestExemptPaths` — 5
  6. `tests/test_api.py::TestHealth` — 6
  7. `tests/test_cli.py` — 3 (health help + cmd_health pretty/compact)
- **`uv run ruff check src/ tests/`:** clean.
- Coverage target (85%) held; the new modules (`stats.py`,
  `liveness.py`) are at or near 100% line coverage.

## Follow-ups

1. **Hitting `/health` on a real server.** The full pipeline
   isn't exercised by tests — the tests build a Starlette
   `TestClient` against `FastMCP.streamable_http_app()` directly
   and don't go through `mcp_server.main()`. Once the next
   session starts the server locally, `curl http://127.0.0.1:8400/health`
   should return the envelope without a bearer token.
2. **Reverse proxy exemption.** If `/health` ever gets proxied
   to an external caller, the proxy config needs to either
   exclude `/health` or scrub the `queries.by_type` block. Noted
   in `docs/api.md`.
3. **Dashboard consumes these.** When Tier 1 item 3a starts, it
   could either call `/health` directly (leaks the same
   zero-content aggregates) or build its own endpoint. The
   endpoint-reuse idea is explicitly punted until the dashboard
   session.
