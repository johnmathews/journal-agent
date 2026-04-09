"""Tests for MCP server lifespan initialization."""

from unittest.mock import MagicMock, patch

import pytest

import journal.mcp_server as mcp_module
from journal.mcp_server import lifespan


@pytest.fixture(autouse=True)
def _reset_services():
    """Reset the global services singleton between tests."""
    mcp_module._services = None
    yield
    mcp_module._services = None


@pytest.fixture
def _mock_chromadb():
    """Patch ChromaVectorStore so tests don't need a running ChromaDB."""
    with patch("journal.mcp_server.ChromaVectorStore") as mock_cls:
        mock_cls.return_value = MagicMock()
        yield mock_cls


async def test_first_call_initializes(monkeypatch, config, _mock_chromadb):
    monkeypatch.setattr("journal.mcp_server.load_config", lambda: config)

    async with lifespan(None) as services:
        assert "ingestion" in services
        assert "query" in services


async def test_second_call_reuses(monkeypatch, config, _mock_chromadb):
    monkeypatch.setattr("journal.mcp_server.load_config", lambda: config)

    async with lifespan(None) as first:
        pass

    async with lifespan(None) as second:
        assert first is second


async def test_config_loaded_once(monkeypatch, config, _mock_chromadb):
    call_count = 0
    original_config = config

    def counting_load():
        nonlocal call_count
        call_count += 1
        return original_config

    monkeypatch.setattr("journal.mcp_server.load_config", counting_load)

    async with lifespan(None):
        pass
    async with lifespan(None):
        pass

    assert call_count == 1
