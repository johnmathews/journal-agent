"""Tests for the in-process stats collector."""

from __future__ import annotations

import threading

from journal.services.stats import InMemoryStatsCollector


class TestRecordAndSnapshot:
    def test_empty_snapshot(self) -> None:
        c = InMemoryStatsCollector()
        snap = c.snapshot()
        assert snap.total_queries == 0
        assert snap.by_type == {}
        assert snap.uptime_seconds >= 0.0
        assert snap.started_at.endswith("+00:00")

    def test_single_query_recorded(self) -> None:
        c = InMemoryStatsCollector()
        c.record_query("semantic_search", 12.5)
        snap = c.snapshot()
        assert snap.total_queries == 1
        assert "semantic_search" in snap.by_type
        type_stats = snap.by_type["semantic_search"]
        assert type_stats.count == 1
        # With a single sample every percentile collapses to that value.
        assert type_stats.latency.p50_ms == 12.5
        assert type_stats.latency.p95_ms == 12.5
        assert type_stats.latency.p99_ms == 12.5

    def test_multiple_types_are_tracked_separately(self) -> None:
        c = InMemoryStatsCollector()
        c.record_query("semantic_search", 10.0)
        c.record_query("semantic_search", 20.0)
        c.record_query("keyword_search", 5.0)
        snap = c.snapshot()
        assert snap.total_queries == 3
        assert snap.by_type["semantic_search"].count == 2
        assert snap.by_type["keyword_search"].count == 1

    def test_negative_latency_is_clamped_to_zero(self) -> None:
        c = InMemoryStatsCollector()
        c.record_query("x", -50.0)
        type_stats = c.snapshot().by_type["x"]
        assert type_stats.latency.p50_ms == 0.0


class TestPercentileComputation:
    def test_percentiles_on_100_samples(self) -> None:
        c = InMemoryStatsCollector()
        # Samples 1..100 — nearest-rank p50 is sample[49] = 50.0.
        for ms in range(1, 101):
            c.record_query("q", float(ms))
        type_stats = c.snapshot().by_type["q"]
        assert type_stats.count == 100
        # Nearest-rank: p50 ≈ idx 49 (value 50), p95 ≈ idx 94 (95),
        # p99 ≈ idx 98 (99). Exact values depend on the formula but
        # we assert they're in a tight window around expected.
        assert 45.0 <= (type_stats.latency.p50_ms or 0) <= 55.0
        assert 93.0 <= (type_stats.latency.p95_ms or 0) <= 97.0
        assert 97.0 <= (type_stats.latency.p99_ms or 0) <= 100.0

    def test_percentiles_are_empty_without_samples(self) -> None:
        c = InMemoryStatsCollector()
        snap = c.snapshot()
        assert snap.by_type == {}


class TestBoundedMemory:
    def test_buffer_is_capped_but_counter_keeps_growing(self) -> None:
        c = InMemoryStatsCollector()
        # Push 1500 samples — buffer should cap at 1000 but counter
        # should be exact (1500). Samples are increasing, so the
        # oldest (small values) get dropped and the max survives.
        for ms in range(1, 1501):
            c.record_query("q", float(ms))
        type_stats = c.snapshot().by_type["q"]
        assert type_stats.count == 1500
        # p99 should reflect the recent (large) samples.
        assert (type_stats.latency.p99_ms or 0) > 1400


class TestThreadSafety:
    def test_concurrent_record_does_not_corrupt_counts(self) -> None:
        c = InMemoryStatsCollector()
        n_threads = 8
        per_thread = 250

        def worker() -> None:
            for i in range(per_thread):
                c.record_query("x", float(i))

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = c.snapshot()
        assert snap.total_queries == n_threads * per_thread
        assert snap.by_type["x"].count == n_threads * per_thread


class TestUptime:
    def test_uptime_is_non_negative(self) -> None:
        c = InMemoryStatsCollector()
        assert c.snapshot().uptime_seconds >= 0.0

    def test_uptime_increases_between_snapshots(self) -> None:
        import time

        c = InMemoryStatsCollector()
        first = c.snapshot().uptime_seconds
        time.sleep(0.01)
        second = c.snapshot().uptime_seconds
        assert second >= first
