"""Tests for per-component liveness checks."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

from journal.services.liveness import (
    check_api_key,
    check_chromadb,
    check_sqlite,
    overall_status,
)


class TestSQLiteCheck:
    def test_ok_on_working_connection(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = check_sqlite(conn)
        assert result.name == "sqlite"
        assert result.status == "ok"
        assert result.error is None

    def test_error_on_closed_connection(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.close()
        result = check_sqlite(conn)
        assert result.status == "error"
        assert result.error is not None


class TestChromaDBCheck:
    def test_ok_with_count(self) -> None:
        store = MagicMock()
        store.count.return_value = 42
        result = check_chromadb(store)
        assert result.status == "ok"
        assert "42" in result.detail

    def test_error_when_count_raises(self) -> None:
        store = MagicMock()
        store.count.side_effect = RuntimeError("connection refused")
        result = check_chromadb(store)
        assert result.status == "error"
        assert result.error == "connection refused"


class TestAPIKeyCheck:
    def test_degraded_when_missing(self) -> None:
        result = check_api_key("anthropic", None)
        assert result.status == "degraded"

    def test_degraded_when_empty(self) -> None:
        result = check_api_key("anthropic", "")
        assert result.status == "degraded"

    def test_degraded_when_too_short(self) -> None:
        result = check_api_key("anthropic", "short", min_length=20)
        assert result.status == "degraded"
        assert "shorter" in result.detail

    def test_ok_with_plausible_key(self) -> None:
        # 40-char string — Anthropic keys in reality are longer
        # but the check only enforces min_length.
        result = check_api_key("anthropic", "a" * 40)
        assert result.status == "ok"
        assert "40 chars" in result.detail


class TestOverallStatus:
    def test_all_ok_is_ok(self) -> None:
        from journal.services.liveness import ComponentCheck

        checks = [
            ComponentCheck("a", "ok", "fine"),
            ComponentCheck("b", "ok", "fine"),
        ]
        assert overall_status(checks) == "ok"

    def test_any_degraded_is_degraded(self) -> None:
        from journal.services.liveness import ComponentCheck

        checks = [
            ComponentCheck("a", "ok", "fine"),
            ComponentCheck("b", "degraded", "meh"),
        ]
        assert overall_status(checks) == "degraded"

    def test_any_error_wins_over_degraded(self) -> None:
        from journal.services.liveness import ComponentCheck

        checks = [
            ComponentCheck("a", "degraded", "meh"),
            ComponentCheck("b", "error", "bad"),
        ]
        assert overall_status(checks) == "error"

    def test_empty_list_defaults_to_ok(self) -> None:
        assert overall_status([]) == "ok"
