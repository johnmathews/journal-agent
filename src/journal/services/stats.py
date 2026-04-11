"""In-process query latency and count statistics.

A lightweight `StatsCollector` that records one `(query_type,
latency_ms)` sample per `QueryService` call and exposes a
`snapshot()` that the `/health` endpoint surfaces as JSON. The
point is *operational* — "is anything slow?" and "how many
queries are we serving?" — not full observability. If this ever
needs to grow (histograms over time, per-hour rollups, export to
Prometheus), replace this module wholesale behind the Protocol;
do not bolt features onto the in-memory implementation.

Design choices:

1. **Bounded memory.** Every query type has its own bounded
   ring-buffer-like list of the last N=1000 latency samples.
   Total memory is O(types × 1000 × 8 bytes) — a few hundred
   kilobytes in the worst case. Running longer than 1000
   queries per type discards the oldest samples; the counter
   is separate and exact for as long as the process lives.
2. **Percentiles on snapshot.** We compute p50/p95/p99 from a
   sorted copy of the buffer at snapshot time, not via an HDR
   histogram. O(n log n) on n ≤ 1000 is fast enough that
   `/health` is still sub-millisecond, and it avoids pulling
   in another dependency.
3. **Thread safety.** FastMCP dispatches request handlers on an
   asyncio event loop, but the historical record of
   `_handle_request_async` from FastMCP suggests some adapters
   push work to a thread pool via `run_in_executor`. A single
   `threading.Lock` around record and snapshot is a tiny
   overhead (microseconds) and makes the data structure safe
   to call from any context.
4. **Zero-overhead opt-in.** `QueryService` accepts an optional
   `stats: StatsCollector | None = None`. When `None`, no
   wrapping, no locks, no allocations — the production path is
   identical to before this module existed.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

_MAX_SAMPLES_PER_TYPE = 1000


@dataclass
class LatencyPercentiles:
    """p50 / p95 / p99 for a single query type, in milliseconds.

    All three are `None` when no samples have been recorded for
    the type. Callers treat the absence of samples as "no data
    yet" rather than "fast".
    """

    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


@dataclass
class QueryTypeStats:
    count: int
    latency: LatencyPercentiles


@dataclass
class StatsSnapshot:
    """A point-in-time view of the stats collector.

    - `total_queries`: sum of all counters since process start.
    - `by_type`: per-type counter and latency percentiles.
    - `uptime_seconds`: wall-clock seconds since the collector
      was constructed (i.e. server start, since the MCP server
      instantiates it at boot).
    - `started_at`: ISO-8601 UTC timestamp of server start.
    """

    total_queries: int
    by_type: dict[str, QueryTypeStats]
    uptime_seconds: float
    started_at: str


@runtime_checkable
class StatsCollector(Protocol):
    def record_query(self, query_type: str, latency_ms: float) -> None: ...

    def snapshot(self) -> StatsSnapshot: ...


@dataclass
class _TypeBucket:
    count: int = 0
    samples: deque[float] = field(
        default_factory=lambda: deque(maxlen=_MAX_SAMPLES_PER_TYPE)
    )


class InMemoryStatsCollector:
    """Default `StatsCollector` implementation — see module docstring."""

    def __init__(self, now: float | None = None) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _TypeBucket] = {}
        self._started_monotonic = now if now is not None else time.monotonic()
        self._started_at_iso = datetime.now(UTC).isoformat(timespec="seconds")

    def record_query(self, query_type: str, latency_ms: float) -> None:
        """Record one query. `latency_ms` must be a non-negative float;
        negatives are clamped to 0 rather than raising — a monotonic
        clock skew should not break the server."""
        ms = max(0.0, float(latency_ms))
        with self._lock:
            bucket = self._buckets.get(query_type)
            if bucket is None:
                bucket = _TypeBucket()
                self._buckets[query_type] = bucket
            bucket.count += 1
            bucket.samples.append(ms)

    def snapshot(self) -> StatsSnapshot:
        with self._lock:
            total = sum(b.count for b in self._buckets.values())
            by_type: dict[str, QueryTypeStats] = {}
            for name, bucket in self._buckets.items():
                by_type[name] = QueryTypeStats(
                    count=bucket.count,
                    latency=_percentiles(bucket.samples),
                )
            uptime = max(0.0, time.monotonic() - self._started_monotonic)
        return StatsSnapshot(
            total_queries=total,
            by_type=by_type,
            uptime_seconds=round(uptime, 3),
            started_at=self._started_at_iso,
        )


def _percentiles(samples: deque[float]) -> LatencyPercentiles:
    """Compute p50/p95/p99 from a small buffer.

    Uses the "nearest-rank" definition of percentile: `p50` is
    the sample at index `ceil(0.50 * n) - 1`, clamped to
    `[0, n-1]`. For n < 20 the p99 is degenerate (it equals the
    max), which matches how we want to treat small samples
    anyway — at low throughput, "p99" is just "the worst one
    we've seen recently".
    """
    n = len(samples)
    if n == 0:
        return LatencyPercentiles(p50_ms=None, p95_ms=None, p99_ms=None)
    sorted_samples = sorted(samples)

    def at(q: float) -> float:
        idx = max(0, min(n - 1, int((q * n) - 1) if q * n >= 1 else 0))
        return sorted_samples[idx]

    return LatencyPercentiles(
        p50_ms=round(at(0.50), 3),
        p95_ms=round(at(0.95), 3),
        p99_ms=round(at(0.99), 3),
    )
