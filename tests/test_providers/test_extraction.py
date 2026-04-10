"""Tests for the Anthropic entity extraction provider."""

from unittest.mock import MagicMock, patch

from journal.providers.extraction import (
    ENTITY_EXTRACTION_TOOL,
    AnthropicExtractionProvider,
    ExtractionProvider,
    RawExtractionResult,
    _parse_tool_response,
    build_system_prompt,
)


def _make_provider() -> AnthropicExtractionProvider:
    with patch("journal.providers.extraction.anthropic.Anthropic"):
        return AnthropicExtractionProvider(
            api_key="test-key",
            model="claude-opus-4-6",
            max_tokens=4096,
        )


class TestAnthropicExtractionProvider:
    def test_implements_protocol(self) -> None:
        provider = _make_provider()
        assert isinstance(provider, ExtractionProvider)

    def test_extract_entities_calls_messages_create_with_tool_choice(
        self,
    ) -> None:
        provider = _make_provider()
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {"entities": [], "relationships": []}
        mock_message = MagicMock()
        mock_message.content = [tool_block]
        provider._client.messages.create.return_value = mock_message

        result = provider.extract_entities(
            entry_text="I went to Vienna with Atlas.",
            entry_date="2026-03-22",
            author_name="John",
        )
        assert isinstance(result, RawExtractionResult)

        kwargs = provider._client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-opus-4-6"
        assert kwargs["tool_choice"] == {
            "type": "tool",
            "name": "record_entities",
        }
        assert kwargs["tools"] == [ENTITY_EXTRACTION_TOOL]
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        # The author name must appear in the system prompt.
        assert "John" in kwargs["system"][0]["text"]

    def test_response_parsing_round_trip(self) -> None:
        provider = _make_provider()
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {
            "entities": [
                {
                    "entity_type": "person",
                    "canonical_name": "Atlas",
                    "description": "a dog",
                    "aliases": ["Atty"],
                    "quote": "Atlas was excited",
                    "confidence": 0.95,
                }
            ],
            "relationships": [
                {
                    "subject": "John",
                    "predicate": "visited",
                    "object": "Vienna",
                    "quote": "I went to Vienna",
                    "confidence": 0.9,
                }
            ],
        }
        mock_message = MagicMock()
        mock_message.content = [tool_block]
        provider._client.messages.create.return_value = mock_message

        result = provider.extract_entities(
            "I went to Vienna with Atlas.", "2026-03-22", "John"
        )
        assert len(result.entities) == 1
        assert result.entities[0]["canonical_name"] == "Atlas"
        assert result.entities[0]["aliases"] == ["Atty"]
        assert result.entities[0]["confidence"] == 0.95
        assert len(result.relationships) == 1
        assert result.relationships[0]["predicate"] == "visited"

    def test_empty_entities_and_relationships_handled(self) -> None:
        provider = _make_provider()
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {"entities": [], "relationships": []}
        mock_message = MagicMock()
        mock_message.content = [tool_block]
        provider._client.messages.create.return_value = mock_message

        result = provider.extract_entities("nothing here", "2026-03-22", "John")
        assert result.entities == []
        assert result.relationships == []

    def test_system_prompt_lists_entity_types_and_author(self) -> None:
        prompt = build_system_prompt("Jane")
        assert "Jane" in prompt
        for t in ("person", "place", "activity", "organization", "topic", "other"):
            assert t in prompt


class TestParseToolResponse:
    def test_none_message_returns_empty(self) -> None:
        result = _parse_tool_response(None)
        assert result.entities == []
        assert result.relationships == []

    def test_missing_content_returns_empty(self) -> None:
        mock = MagicMock()
        mock.content = None
        result = _parse_tool_response(mock)
        assert result.entities == []

    def test_prefers_tool_use_block(self) -> None:
        text_block = MagicMock()
        text_block.type = "text"
        text_block.input = {"entities": [{"canonical_name": "wrong"}]}
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {
            "entities": [
                {
                    "entity_type": "person",
                    "canonical_name": "Atlas",
                    "confidence": 0.5,
                    "quote": "",
                }
            ],
            "relationships": [],
        }
        mock = MagicMock()
        mock.content = [text_block, tool_block]
        result = _parse_tool_response(mock)
        assert len(result.entities) == 1
        assert result.entities[0]["canonical_name"] == "Atlas"

    def test_skips_non_dict_items(self) -> None:
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {
            "entities": ["not a dict", {"canonical_name": "Atlas", "confidence": 0.1}],
            "relationships": [],
        }
        mock = MagicMock()
        mock.content = [tool_block]
        result = _parse_tool_response(mock)
        assert len(result.entities) == 1
