"""Query service — orchestrates hybrid search and stat/list lookups.

The keyword/semantic mode toggle was retired when hybrid search shipped.
`search_entries` now runs the full L1 (BM25 + dense) → RRF → L2 rerank
pipeline; the `keyword_search` and semantic-only paths are gone.
Callers (REST API, MCP tool, CLI) get a single search method that does
the right thing without forcing a mode choice on the user.

Other read methods (statistics, mood trends, topic frequency, list /
get-by-date) are unchanged — they delegate to the repository and
optionally record latency through `StatsCollector`.
"""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from journal.db.repository import EntryRepository
from journal.models import (
    Entry,
    EntryPage,
    MoodTrend,
    SearchResult,
    Statistics,
    TopicFrequency,
)
from journal.providers.embeddings import EmbeddingsProvider
from journal.providers.reranker import NoopReranker, Reranker
from journal.services.hybrid import HybridConfig, HybridSearchService
from journal.services.stats import StatsCollector
from journal.vectorstore.store import VectorStore

log = logging.getLogger(__name__)

T = TypeVar("T")


class QueryService:
    def __init__(
        self,
        repository: EntryRepository,
        vector_store: VectorStore,
        embeddings_provider: EmbeddingsProvider,
        stats: StatsCollector | None = None,
        reranker: Reranker | None = None,
        hybrid_config: HybridConfig | None = None,
    ) -> None:
        self._repo = repository
        # Kept as an attribute (not strictly needed for query routing)
        # so the /health endpoint and other diagnostics that reach in
        # for `query_svc._vector_store` continue to work.
        self._vector_store = vector_store
        self._stats = stats
        self._hybrid = HybridSearchService(
            repository=repository,
            vector_store=vector_store,
            embeddings_provider=embeddings_provider,
            reranker=reranker or NoopReranker(),
            config=hybrid_config,
            stats=stats,
        )

    @property
    def hybrid(self) -> HybridSearchService:
        """Expose the underlying hybrid service for diagnostics and admin."""
        return self._hybrid

    def _timed(self, query_type: str, fn: Callable[[], T]) -> T:
        """Run `fn()` and record its latency under `query_type`.

        If no stats collector is configured, this is a direct call
        with no clock reads.
        """
        if self._stats is None:
            return fn()
        start = time.monotonic()
        try:
            return fn()
        finally:
            latency_ms = (time.monotonic() - start) * 1000.0
            self._stats.record_query(query_type, latency_ms)

    def search_entries(
        self,
        query: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
        offset: int = 0,
        user_id: int | None = None,
        sort: str = "relevance",
    ) -> list[SearchResult]:
        """Hybrid search across journal entries.

        Runs BM25 (FTS5) and dense embedding retrieval in parallel,
        fuses the rankings with Reciprocal Rank Fusion, and reranks
        the top-M candidates with the configured reranker.

        Returns one `SearchResult` per matching entry. Each carries:
        - `snippet` if BM25 contributed (FTS5-marked excerpt).
        - `matching_chunks` if dense retrieval contributed.
        Either or both may be present. The list is ordered by post-
        rerank score descending, then sliced by `offset` / `limit`.

        `sort` overrides the final ordering: "relevance" (default)
        preserves the rerank order; "date_desc" / "date_asc" sort by
        `entry_date` before the slice.
        """
        return self._hybrid.search(
            query=query,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
            user_id=user_id,
            sort=sort,
        )

    def get_entries_by_date(
        self, date: str, user_id: int | None = None
    ) -> list[Entry]:
        return self._repo.get_entries_by_date(date, user_id=user_id)

    def list_entries(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
        offset: int = 0,
        user_id: int | None = None,
    ) -> list[Entry]:
        return self._repo.list_entries(
            start_date, end_date, limit, offset, user_id=user_id
        )

    def get_statistics(
        self, start_date: str | None = None, end_date: str | None = None,
        user_id: int | None = None,
    ) -> Statistics:
        return self._timed(
            "statistics",
            lambda: self._repo.get_statistics(
                start_date, end_date, user_id=user_id
            ),
        )

    def get_mood_trends(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        granularity: str = "week",
        user_id: int | None = None,
    ) -> list[MoodTrend]:
        return self._timed(
            "mood_trends",
            lambda: self._repo.get_mood_trends(
                start_date, end_date, granularity, user_id=user_id
            ),
        )

    def get_topic_frequency(
        self, topic: str, start_date: str | None = None, end_date: str | None = None,
        user_id: int | None = None,
    ) -> TopicFrequency:
        return self._timed(
            "topic_frequency",
            lambda: self._repo.get_topic_frequency(
                topic, start_date, end_date, user_id=user_id
            ),
        )

    def get_entry_pages(self, entry_id: int) -> list[EntryPage]:
        return self._repo.get_entry_pages(entry_id)
