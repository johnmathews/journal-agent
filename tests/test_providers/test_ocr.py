"""Tests for the Anthropic OCR provider."""

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from journal.providers.ocr import (
    CONTEXT_USAGE_INSTRUCTIONS,
    SYSTEM_PROMPT,
    AnthropicOCRProvider,
    OCRProvider,
    _build_cache_control,
    load_context_files,
)


class TestAnthropicOCRProvider:
    """Tests for AnthropicOCRProvider."""

    def _make_provider(
        self,
        context_dir: Path | None = None,
        cache_ttl: str = "5m",
    ) -> AnthropicOCRProvider:
        with patch("journal.providers.ocr.anthropic.Anthropic"):
            provider = AnthropicOCRProvider(
                api_key="test-key",
                model="claude-opus-4-6",
                max_tokens=4096,
                context_dir=context_dir,
                cache_ttl=cache_ttl,
            )
        return provider

    def test_implements_protocol(self) -> None:
        provider = self._make_provider()
        assert isinstance(provider, OCRProvider)

    def test_extract_text_success(self) -> None:
        provider = self._make_provider()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Hello world from handwriting")]
        provider._client.messages.create.return_value = mock_message

        result = provider.extract_text(b"fake-image-data", "image/png")

        assert result == "Hello world from handwriting"
        provider._client.messages.create.assert_called_once()

    def test_system_prompt_included_without_context(self) -> None:
        provider = self._make_provider()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="extracted")]
        provider._client.messages.create.return_value = mock_message

        provider.extract_text(b"fake-image-data", "image/jpeg")

        call_kwargs = provider._client.messages.create.call_args.kwargs
        system = call_kwargs["system"]
        assert len(system) == 1
        # Without a context dir, the system block is the unchanged
        # SYSTEM_PROMPT — no glossary instructions appended.
        assert system[0]["text"] == SYSTEM_PROMPT
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_image_is_base64_encoded(self) -> None:
        provider = self._make_provider()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="extracted")]
        provider._client.messages.create.return_value = mock_message

        image_data = b"fake-image-data"
        provider.extract_text(image_data, "image/png")

        call_kwargs = provider._client.messages.create.call_args.kwargs
        messages = call_kwargs["messages"]
        image_block = messages[0]["content"][0]
        expected_b64 = base64.standard_b64encode(image_data).decode("utf-8")
        assert image_block["source"]["data"] == expected_b64
        assert image_block["source"]["media_type"] == "image/png"

    def test_context_dir_composes_into_system_text(
        self, tmp_path: Path
    ) -> None:
        context = tmp_path / "context"
        context.mkdir()
        (context / "people.md").write_text(
            "- Ritsya — daughter\n- Atlas — dog\n"
        )
        (context / "places.md").write_text(
            "- Vienna — first met Atlas here\n"
        )

        provider = self._make_provider(context_dir=context)
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="extracted")]
        provider._client.messages.create.return_value = mock_message

        provider.extract_text(b"data", "image/png")

        system_text = (
            provider._client.messages.create.call_args.kwargs["system"][0]["text"]
        )
        # Start matches the original prompt.
        assert system_text.startswith(SYSTEM_PROMPT)
        # Hallucination-prevention instructions come next.
        assert CONTEXT_USAGE_INSTRUCTIONS.strip() in system_text
        # Both context files are present, in alphabetical order.
        people_idx = system_text.find("people")
        places_idx = system_text.find("places")
        assert people_idx != -1 and places_idx != -1
        assert people_idx < places_idx
        # Content from the files is verbatim in the system text.
        assert "Ritsya" in system_text
        assert "Atlas" in system_text
        assert "Vienna" in system_text

    def test_context_dir_missing_falls_back_to_system_prompt(
        self, tmp_path: Path
    ) -> None:
        # Point at a dir that doesn't exist — provider must fall back.
        missing = tmp_path / "nope"
        provider = self._make_provider(context_dir=missing)
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="extracted")]
        provider._client.messages.create.return_value = mock_message
        provider.extract_text(b"data", "image/png")

        system_text = (
            provider._client.messages.create.call_args.kwargs["system"][0]["text"]
        )
        assert system_text == SYSTEM_PROMPT

    def test_context_dir_empty_falls_back_to_system_prompt(
        self, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        provider = self._make_provider(context_dir=empty)
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="extracted")]
        provider._client.messages.create.return_value = mock_message
        provider.extract_text(b"data", "image/png")

        system_text = (
            provider._client.messages.create.call_args.kwargs["system"][0]["text"]
        )
        assert system_text == SYSTEM_PROMPT

    def test_cache_ttl_1h(self) -> None:
        provider = self._make_provider(cache_ttl="1h")
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="extracted")]
        provider._client.messages.create.return_value = mock_message
        provider.extract_text(b"data", "image/png")

        cache_control = (
            provider._client.messages.create.call_args.kwargs["system"][0][
                "cache_control"
            ]
        )
        assert cache_control == {"type": "ephemeral", "ttl": "1h"}

    def test_invalid_cache_ttl_raises(self) -> None:
        with (
            patch("journal.providers.ocr.anthropic.Anthropic"),
            pytest.raises(ValueError, match="Invalid OCR context cache TTL"),
        ):
            AnthropicOCRProvider(
                api_key="test-key",
                model="claude-opus-4-6",
                max_tokens=4096,
                cache_ttl="30m",
            )

    def test_small_context_logs_cache_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A small glossary is well under the 4096-token cache minimum —
        # the provider should log a warning on init.
        context = tmp_path / "context"
        context.mkdir()
        (context / "people.md").write_text("- Ritsya\n")

        with caplog.at_level("WARNING", logger="journal.providers.ocr"):
            self._make_provider(context_dir=context)

        messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("cache minimum" in m for m in messages), (
            f"expected a cache-minimum warning, got: {messages}"
        )


class TestLoadContextFiles:
    def test_none_returns_empty(self) -> None:
        assert load_context_files(None) == ""

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert load_context_files(tmp_path / "missing") == ""

    def test_not_a_directory_returns_empty(self, tmp_path: Path) -> None:
        # A file, not a directory.
        p = tmp_path / "not-a-dir.md"
        p.write_text("content")
        assert load_context_files(p) == ""

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert load_context_files(empty) == ""

    def test_alphabetical_order_and_headers(self, tmp_path: Path) -> None:
        d = tmp_path / "ctx"
        d.mkdir()
        (d / "zebra.md").write_text("z content")
        (d / "apple.md").write_text("a content")
        result = load_context_files(d)
        # Headers derived from filename stems.
        assert "# apple" in result
        assert "# zebra" in result
        # Alphabetical: apple before zebra.
        assert result.find("# apple") < result.find("# zebra")

    def test_underscores_and_dashes_become_spaces_in_heading(
        self, tmp_path: Path
    ) -> None:
        d = tmp_path / "ctx"
        d.mkdir()
        (d / "work_topics.md").write_text("a")
        (d / "family-names.md").write_text("b")
        result = load_context_files(d)
        assert "# work topics" in result
        assert "# family names" in result

    def test_empty_file_is_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "ctx"
        d.mkdir()
        (d / "empty.md").write_text("")
        (d / "real.md").write_text("content")
        result = load_context_files(d)
        assert "# empty" not in result
        assert "# real" in result

    def test_non_md_files_ignored(self, tmp_path: Path) -> None:
        d = tmp_path / "ctx"
        d.mkdir()
        (d / "notes.txt").write_text("should be ignored")
        (d / "glossary.md").write_text("should be included")
        result = load_context_files(d)
        assert "should be ignored" not in result
        assert "should be included" in result


class TestBuildCacheControl:
    def test_5m_default(self) -> None:
        assert _build_cache_control("5m") == {"type": "ephemeral"}

    def test_1h_adds_ttl(self) -> None:
        assert _build_cache_control("1h") == {
            "type": "ephemeral",
            "ttl": "1h",
        }

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid OCR context cache TTL"):
            _build_cache_control("10m")
