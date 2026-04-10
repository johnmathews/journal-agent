"""Tests for the backfill service."""

import pytest

from journal.db.repository import SQLiteEntryRepository
from journal.services.backfill import BackfillResult, backfill_chunk_counts
from journal.services.chunking import FixedTokenChunker


@pytest.fixture
def repo(db_conn):
    return SQLiteEntryRepository(db_conn)


@pytest.fixture
def chunker():
    return FixedTokenChunker(max_tokens=150, overlap_tokens=40)


def _insert(repo: SQLiteEntryRepository, text: str, *, final_text: str | None = None):
    """Helper: insert an entry and (optionally) set final_text, always with chunk_count=0."""
    entry = repo.create_entry("2026-03-01", "ocr", text, len(text.split()))
    if final_text is not None:
        repo.update_final_text(entry.id, final_text, len(final_text.split()), 0)
    # Force stale chunk_count so backfill has something to do.
    repo.update_chunk_count(entry.id, 0)
    return entry


class TestBackfillChunkCounts:
    def test_sets_chunk_count_from_raw_text(self, repo, chunker):
        entry = _insert(repo, "Short seeded entry text.")

        result = backfill_chunk_counts(repo, chunker)

        refreshed = repo.get_entry(entry.id)
        assert refreshed is not None
        assert refreshed.chunk_count >= 1
        assert result.updated == 1
        assert result.unchanged == 0
        assert result.skipped == 0
        assert result.errors == []

    def test_prefers_final_text_over_raw_text(self, repo, chunker):
        entry = _insert(
            repo,
            "raw",
            final_text="This is the corrected version of the entry with more words.",
        )

        result = backfill_chunk_counts(repo, chunker)

        refreshed = repo.get_entry(entry.id)
        assert refreshed is not None
        assert refreshed.chunk_count >= 1
        assert result.updated == 1

    def test_leaves_already_correct_rows_unchanged(self, repo, chunker):
        entry = _insert(repo, "Short text.")
        # Run once to populate the correct count, then again.
        backfill_chunk_counts(repo, chunker)

        result = backfill_chunk_counts(repo, chunker)

        assert result.updated == 0
        assert result.unchanged == 1
        refreshed = repo.get_entry(entry.id)
        assert refreshed is not None
        assert refreshed.chunk_count >= 1

    def test_skips_entries_with_no_text(self, repo, chunker):
        entry = repo.create_entry("2026-03-02", "ocr", "", 0)
        repo.update_chunk_count(entry.id, 0)

        result = backfill_chunk_counts(repo, chunker)

        assert result.skipped == 1
        assert result.updated == 0
        refreshed = repo.get_entry(entry.id)
        assert refreshed is not None
        assert refreshed.chunk_count == 0

    def test_handles_long_text_producing_multiple_chunks(self, repo):
        long_paragraph = (
            "Sentence one with a few words. " * 60
        ).strip()  # ~360 words, definitely > 150 tokens
        entry = _insert(repo, long_paragraph)

        backfill_chunk_counts(
            repo, FixedTokenChunker(max_tokens=150, overlap_tokens=40)
        )

        refreshed = repo.get_entry(entry.id)
        assert refreshed is not None
        assert refreshed.chunk_count > 1

    def test_processes_multiple_entries(self, repo, chunker):
        _insert(repo, "First entry.")
        _insert(repo, "Second entry with more words in it.")
        _insert(repo, "Third entry also short.")

        result = backfill_chunk_counts(repo, chunker)

        assert result.updated == 3
        assert result.unchanged == 0

    def test_chunker_exception_is_captured_in_errors(self, repo):
        _insert(repo, "Entry one.")
        _insert(repo, "Entry two.")

        # Build a flaky chunker that raises on the second call.
        call_count = {"n": 0}
        real_chunker = FixedTokenChunker(max_tokens=150, overlap_tokens=40)

        class FlakyChunker:
            def chunk(self, text: str) -> list[str]:
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise RuntimeError("boom")
                return real_chunker.chunk(text)

        result = backfill_chunk_counts(repo, FlakyChunker())

        assert result.updated == 1
        assert len(result.errors) == 1
        assert "boom" in result.errors[0]

    def test_result_dataclass_defaults(self):
        r = BackfillResult()
        assert r.updated == 0
        assert r.unchanged == 0
        assert r.skipped == 0
        assert r.errors == []
