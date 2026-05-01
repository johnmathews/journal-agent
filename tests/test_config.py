"""Tests for configuration loading."""


import pytest

from journal.config import Config


class TestAllowedHosts:
    def test_default_allowed_hosts_loopback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Default (no env var) must be loopback-only so DNS rebinding
        # protection is always meaningful. An empty list would have
        # previously let mcp_server disable the protection entirely.
        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
        config = Config()
        assert config.mcp_allowed_hosts == ["127.0.0.1", "localhost"]

    def test_allowed_hosts_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MCP_ALLOWED_HOSTS", "192.168.2.105:8000,localhost:8000"
        )
        config = Config()
        assert config.mcp_allowed_hosts == [
            "192.168.2.105:8000",
            "localhost:8000",
        ]

    def test_allowed_hosts_strips_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MCP_ALLOWED_HOSTS", " 192.168.2.105:8000 , localhost:8000 "
        )
        config = Config()
        assert config.mcp_allowed_hosts == [
            "192.168.2.105:8000",
            "localhost:8000",
        ]

    def test_allowed_hosts_ignores_empty_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "192.168.2.105:8000,,")
        config = Config()
        assert config.mcp_allowed_hosts == ["192.168.2.105:8000"]

    def test_allowed_hosts_wildcard_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "192.168.2.105:*")
        config = Config()
        assert config.mcp_allowed_hosts == ["192.168.2.105:*"]


class TestOcrContext:
    def test_default_context_dir_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OCR_CONTEXT_DIR", raising=False)
        config = Config()
        assert config.ocr_context_dir is None

    def test_context_dir_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        monkeypatch.setenv("OCR_CONTEXT_DIR", "/etc/journal/context")
        config = Config()
        assert config.ocr_context_dir is not None
        assert str(config.ocr_context_dir) == "/etc/journal/context"

    def test_default_cache_ttl_is_1h(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OCR_CONTEXT_CACHE_TTL", raising=False)
        config = Config()
        assert config.ocr_context_cache_ttl == "1h"

    def test_cache_ttl_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCR_CONTEXT_CACHE_TTL", "5m")
        config = Config()
        assert config.ocr_context_cache_ttl == "5m"


class TestApiBearerToken:
    def test_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No env var → None. `mcp_server.main()` uses this sentinel to
        # fail closed and refuse to start.
        monkeypatch.delenv("JOURNAL_API_TOKEN", raising=False)
        config = Config()
        assert config.api_bearer_token is None

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JOURNAL_API_TOKEN", "abc123")
        config = Config()
        assert config.api_bearer_token == "abc123"

    def test_empty_string_treated_as_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An explicitly empty JOURNAL_API_TOKEN= line in .env must not
        # silently disable auth — it's equivalent to "not set".
        monkeypatch.setenv("JOURNAL_API_TOKEN", "")
        config = Config()
        assert config.api_bearer_token is None


class TestPreprocessImages:
    def test_default_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PREPROCESS_IMAGES", raising=False)
        assert Config().preprocess_images is True

    def test_env_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PREPROCESS_IMAGES", "false")
        assert Config().preprocess_images is False

    def test_env_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PREPROCESS_IMAGES", "0")
        assert Config().preprocess_images is False


class TestOcrDualPass:
    def test_default_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCR_DUAL_PASS", raising=False)
        assert Config().ocr_dual_pass is False

    def test_env_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_DUAL_PASS", "true")
        assert Config().ocr_dual_pass is True

    def test_env_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_DUAL_PASS", "1")
        assert Config().ocr_dual_pass is True


def _clean_transcription_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "TRANSCRIPTION_PROVIDER",
        "TRANSCRIPTION_FALLBACK_ENABLED",
        "TRANSCRIPTION_FALLBACK_MODEL",
        "TRANSCRIPTION_RETRY_MAX_ATTEMPTS",
        "TRANSCRIPTION_RETRY_BASE_DELAY",
        "TRANSCRIPTION_RETRY_MAX_DELAY",
        "TRANSCRIPTION_SHADOW_PROVIDER",
        "TRANSCRIPTION_SHADOW_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


class TestTranscriptionProviderConfig:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_transcription_env(monkeypatch)
        config = Config()
        assert config.transcription_provider == "openai"
        assert config.transcription_fallback_enabled is True
        assert config.transcription_fallback_model == "whisper-1"
        assert config.transcription_retry_max_attempts == 3
        assert config.transcription_retry_base_delay == 1.0
        assert config.transcription_retry_max_delay == 30.0
        assert config.transcription_shadow_provider == ""
        assert config.transcription_shadow_model == ""

    def test_provider_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "gemini")
        assert Config().transcription_provider == "gemini"

    def test_fallback_disabled_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_FALLBACK_ENABLED", "false")
        assert Config().transcription_fallback_enabled is False

    def test_fallback_model_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_FALLBACK_MODEL", "gpt-4o-mini-transcribe")
        assert Config().transcription_fallback_model == "gpt-4o-mini-transcribe"

    def test_retry_settings_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_RETRY_MAX_ATTEMPTS", "5")
        monkeypatch.setenv("TRANSCRIPTION_RETRY_BASE_DELAY", "2.5")
        monkeypatch.setenv("TRANSCRIPTION_RETRY_MAX_DELAY", "60")
        config = Config()
        assert config.transcription_retry_max_attempts == 5
        assert config.transcription_retry_base_delay == 2.5
        assert config.transcription_retry_max_delay == 60.0

    def test_shadow_provider_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_SHADOW_PROVIDER", "gemini")
        monkeypatch.setenv("TRANSCRIPTION_SHADOW_MODEL", "gemini-2.5-flash")
        config = Config()
        assert config.transcription_shadow_provider == "gemini"
        assert config.transcription_shadow_model == "gemini-2.5-flash"

    def test_invalid_provider_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "foo")
        with pytest.raises(ValueError, match="TRANSCRIPTION_PROVIDER"):
            Config()

    def test_invalid_shadow_provider_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_SHADOW_PROVIDER", "foo")
        with pytest.raises(ValueError, match="TRANSCRIPTION_SHADOW_PROVIDER"):
            Config()

    def test_empty_shadow_provider_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_SHADOW_PROVIDER", "")
        # Empty string disables shadow — must not raise.
        Config()

    def test_zero_max_attempts_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_RETRY_MAX_ATTEMPTS", "0")
        with pytest.raises(ValueError, match="TRANSCRIPTION_RETRY_MAX_ATTEMPTS"):
            Config()

    def test_negative_base_delay_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_RETRY_BASE_DELAY", "-1")
        with pytest.raises(ValueError, match="TRANSCRIPTION_RETRY_BASE_DELAY"):
            Config()

    def test_negative_max_delay_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clean_transcription_env(monkeypatch)
        monkeypatch.setenv("TRANSCRIPTION_RETRY_MAX_DELAY", "-5")
        with pytest.raises(ValueError, match="TRANSCRIPTION_RETRY_MAX_DELAY"):
            Config()
