"""Tests for pricing configuration."""

import sqlite3

import pytest

from journal.db.migrations import run_migrations
from journal.db.pricing import PricingEntry, get_all_pricing, update_pricing


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    run_migrations(c)
    return c


class TestGetAllPricing:
    """get_all_pricing — seed data and structure."""

    def test_returns_seed_data(self, conn: sqlite3.Connection) -> None:
        entries = get_all_pricing(conn)
        assert len(entries) >= 12
        models = {e.model for e in entries}
        assert "claude-opus-4-6" in models
        assert "gemini-2.5-pro" in models
        assert "text-embedding-3-large" in models
        assert "gpt-4o-transcribe" in models

    def test_all_entries_are_pricing_entry(self, conn: sqlite3.Connection) -> None:
        entries = get_all_pricing(conn)
        for e in entries:
            assert isinstance(e, PricingEntry)

    def test_categories(self, conn: sqlite3.Connection) -> None:
        entries = get_all_pricing(conn)
        categories = {e.category for e in entries}
        assert categories == {"llm", "embedding", "transcription"}

    def test_llm_entries_have_input_and_output(self, conn: sqlite3.Connection) -> None:
        entries = get_all_pricing(conn)
        llm_entries = [e for e in entries if e.category == "llm"]
        assert len(llm_entries) >= 1
        for e in llm_entries:
            assert e.input_cost_per_mtok is not None and e.input_cost_per_mtok > 0
            assert e.output_cost_per_mtok is not None and e.output_cost_per_mtok > 0
            assert e.cost_per_minute is None

    def test_transcription_entries_have_cost_per_minute(
        self, conn: sqlite3.Connection,
    ) -> None:
        entries = get_all_pricing(conn)
        transcription = [e for e in entries if e.category == "transcription"]
        assert len(transcription) >= 1
        for e in transcription:
            assert e.cost_per_minute is not None and e.cost_per_minute > 0
            assert e.input_cost_per_mtok is None
            assert e.output_cost_per_mtok is None

    def test_embedding_entries_have_zero_output(
        self, conn: sqlite3.Connection,
    ) -> None:
        entries = get_all_pricing(conn)
        embeddings = [e for e in entries if e.category == "embedding"]
        assert len(embeddings) >= 1
        for e in embeddings:
            assert e.input_cost_per_mtok is not None and e.input_cost_per_mtok > 0
            assert e.output_cost_per_mtok == 0

    def test_ordered_by_category_then_model(self, conn: sqlite3.Connection) -> None:
        entries = get_all_pricing(conn)
        keys = [(e.category, e.model) for e in entries]
        assert keys == sorted(keys)


class TestUpdatePricing:
    """update_pricing — modification and validation."""

    def test_changes_costs(self, conn: sqlite3.Connection) -> None:
        result = update_pricing(
            conn, "claude-opus-4-6",
            {"input_cost_per_mtok": 6.0, "output_cost_per_mtok": 30.0},
        )
        assert result is not None
        assert result.input_cost_per_mtok == 6.0
        assert result.output_cost_per_mtok == 30.0

    def test_persists_change(self, conn: sqlite3.Connection) -> None:
        update_pricing(conn, "claude-opus-4-6", {"input_cost_per_mtok": 7.0})
        entries = get_all_pricing(conn)
        opus = next(e for e in entries if e.model == "claude-opus-4-6")
        assert opus.input_cost_per_mtok == 7.0

    def test_unknown_model(self, conn: sqlite3.Connection) -> None:
        result = update_pricing(conn, "nonexistent-model", {"input_cost_per_mtok": 1.0})
        assert result is None

    def test_ignores_disallowed_fields(self, conn: sqlite3.Connection) -> None:
        result = update_pricing(
            conn, "claude-opus-4-6",
            {"category": "embedding", "input_cost_per_mtok": 5.0},
        )
        assert result is not None
        assert result.category == "llm"  # category must not change

    def test_empty_dict(self, conn: sqlite3.Connection) -> None:
        result = update_pricing(conn, "claude-opus-4-6", {})
        assert result is None

    def test_only_disallowed_fields(self, conn: sqlite3.Connection) -> None:
        result = update_pricing(conn, "claude-opus-4-6", {"model": "new-name"})
        assert result is None

    def test_updates_last_verified(self, conn: sqlite3.Connection) -> None:
        result = update_pricing(
            conn, "gemini-2.5-pro",
            {"input_cost_per_mtok": 1.5, "last_verified": "2026-05-01"},
        )
        assert result is not None
        assert result.last_verified == "2026-05-01"
        assert result.input_cost_per_mtok == 1.5

    def test_updates_cost_per_minute(self, conn: sqlite3.Connection) -> None:
        result = update_pricing(
            conn, "gpt-4o-transcribe", {"cost_per_minute": 0.01},
        )
        assert result is not None
        assert result.cost_per_minute == 0.01
