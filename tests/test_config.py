"""Tests for configuration loading."""

import os
from pathlib import Path

from journal.config import Config


class TestConfig:
    def test_default_allowed_hosts_empty(self) -> None:
        config = Config()
        assert config.mcp_allowed_hosts == []

    def test_allowed_hosts_from_env(self, monkeypatch: object) -> None:
        import pytest

        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "192.168.2.105:8000,localhost:8000")  # type: ignore[attr-defined]
        config = Config()
        assert config.mcp_allowed_hosts == ["192.168.2.105:8000", "localhost:8000"]

    def test_allowed_hosts_strips_whitespace(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", " 192.168.2.105:8000 , localhost:8000 ")  # type: ignore[attr-defined]
        config = Config()
        assert config.mcp_allowed_hosts == ["192.168.2.105:8000", "localhost:8000"]

    def test_allowed_hosts_ignores_empty_entries(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "192.168.2.105:8000,,")  # type: ignore[attr-defined]
        config = Config()
        assert config.mcp_allowed_hosts == ["192.168.2.105:8000"]

    def test_allowed_hosts_wildcard_port(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "192.168.2.105:*")  # type: ignore[attr-defined]
        config = Config()
        assert config.mcp_allowed_hosts == ["192.168.2.105:*"]
