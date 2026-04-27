"""Tests for the Anthropic entity extraction provider."""

import logging
from unittest.mock import MagicMock, patch

from journal.providers.extraction import (
    ENTITY_EXTRACTION_TOOL,
    AnthropicExtractionProvider,
    ExtractionProvider,
    RawExtractionResult,
    _parse_tool_response,
    _repair_canonical_name,
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


# ----------------------------------------------------------------------
# canonical_name repair
# ----------------------------------------------------------------------


class TestRepairCanonicalName:
    """The Nautilin/Nautiline class of LLM clipping bug — the model
    occasionally returns a canonical_name that's one or two characters
    shorter than the form actually in the source quote. The validator
    detects this case and repairs to the longer form."""

    def test_canonical_name_in_quote_is_unchanged(self) -> None:
        """Substring match: trust the LLM's choice as-is. Protects
        deliberate short forms like 'Bob' for 'Robert "Bob" Smith'."""
        repaired, was_repaired = _repair_canonical_name(
            "Bob", "Robert 'Bob' Smith said hi",
        )
        assert repaired == "Bob"
        assert was_repaired is False

    def test_clipped_trailing_character_is_repaired(self) -> None:
        """The original prod bug: 'Nautilin' clipped from 'Nautiline'."""
        repaired, was_repaired = _repair_canonical_name(
            "Nautilin",
            "Nautiline, the iOS app that connects to our music server",
        )
        assert repaired == "Nautiline"
        assert was_repaired is True

    def test_no_match_keeps_canonical_unchanged(self) -> None:
        """If canonical_name is neither a substring nor a strict prefix
        of any token, leave it alone — we never invent characters."""
        repaired, was_repaired = _repair_canonical_name(
            "Foo", "Bar baz qux",
        )
        assert repaired == "Foo"
        assert was_repaired is False

    def test_strips_trailing_punctuation_when_comparing(self) -> None:
        """Quotes routinely have commas/periods after names. Compare
        against the bare token after stripping surrounding punctuation.
        Uses a non-inflection extension (an 'a' suffix, not the 's'
        that would be rejected by the inflection guard)."""
        repaired, was_repaired = _repair_canonical_name(
            "Vienn", "I went to Vienna, the capital.",
        )
        assert repaired == "Vienna"
        assert was_repaired is True

    def test_case_insensitive_prefix_returns_original_case(self) -> None:
        """Match case-insensitively on the prefix test, but preserve
        the original casing of the matched token in the return value."""
        repaired, was_repaired = _repair_canonical_name(
            "nautilin",
            "We launched Nautiline yesterday.",
        )
        assert repaired == "Nautiline"
        assert was_repaired is True

    def test_picks_first_matching_token_when_multiple(self) -> None:
        repaired, was_repaired = _repair_canonical_name(
            "Sam", "Samuel and Samantha disagreed.",
        )
        assert repaired == "Samuel"
        assert was_repaired is True

    def test_empty_inputs_are_safe(self) -> None:
        assert _repair_canonical_name("", "anything") == ("", False)
        assert _repair_canonical_name("Atlas", "") == ("Atlas", False)

    def test_token_same_length_or_shorter_is_not_a_repair_candidate(
        self,
    ) -> None:
        """``startswith`` must be a STRICT prefix — equal-length tokens
        should not trigger a repair (and would be a no-op anyway)."""
        repaired, was_repaired = _repair_canonical_name(
            "Atlas", "I saw atlas yesterday",  # same length, just different case
        )
        # Same length, not a strict prefix → no repair
        assert repaired == "Atlas"
        assert was_repaired is False

    def test_possessive_apostrophe_s_is_not_a_repair_candidate(self) -> None:
        """Most common false-positive surfaced by the first prod
        dry-run: 'Hermione' should NOT be promoted to 'Hermione's'
        just because the quote uses the possessive form. The LLM
        correctly picked the bare canonical."""
        repaired, was_repaired = _repair_canonical_name(
            "Hermione", "I was at Hermione's house yesterday.",
        )
        assert repaired == "Hermione"
        assert was_repaired is False

    def test_plural_s_is_not_a_repair_candidate(self) -> None:
        """'Daniel' should not be promoted to 'Daniels' — that's a
        possessive without an apostrophe or a plural form."""
        repaired, was_repaired = _repair_canonical_name(
            "Daniel", "Daniels came over for dinner.",
        )
        assert repaired == "Daniel"
        assert was_repaired is False

    def test_plural_possessive_s_apostrophe_is_not_a_repair_candidate(
        self,
    ) -> None:
        """``s'`` (plural possessive) is also rejected as inflection."""
        repaired, was_repaired = _repair_canonical_name(
            "Smith", "the Smiths' house was crowded",
        )
        assert repaired == "Smith"
        assert was_repaired is False

    def test_real_repair_still_works_when_quote_only_has_clipped_form(
        self,
    ) -> None:
        """Regression check: the original Nautilin/Nautiline case must
        still produce a real repair. The inflection guard rejects only
        possessive/plural extensions, not arbitrary longer tokens."""
        repaired, was_repaired = _repair_canonical_name(
            "Nautilin", "Nautiline shipped today",
        )
        assert repaired == "Nautiline"
        assert was_repaired is True

    def test_inflection_present_short_circuits_other_repairs(self) -> None:
        """If the quote contains an inflection form of the canonical,
        we trust the LLM and stop looking for repair candidates — even
        if a non-inflection longer token also appears later. This is
        the safer choice given how prevalent false-positive repairs
        on inflections were on the first prod dry-run."""
        repaired, was_repaired = _repair_canonical_name(
            "Nautilin",
            "Nautilin's API talks to Nautiline yesterday",
        )
        assert repaired == "Nautilin"
        assert was_repaired is False


class TestParseToolResponseRepairsAndLogs:
    """Integration: when _parse_tool_response sees a clipped
    canonical_name, it repairs it and logs a WARNING."""

    def test_clipped_name_in_payload_is_repaired(
        self, caplog: "logging.LogCaptureFixture",
    ) -> None:
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {
            "entities": [
                {
                    "entity_type": "organization",
                    "canonical_name": "Nautilin",
                    "quote": "Nautiline, the iOS app",
                    "confidence": 0.9,
                },
            ],
            "relationships": [],
        }
        mock = MagicMock()
        mock.content = [tool_block]
        with caplog.at_level(
            logging.WARNING, logger="journal.providers.extraction",
        ):
            result = _parse_tool_response(mock)

        assert len(result.entities) == 1
        assert result.entities[0]["canonical_name"] == "Nautiline"
        assert any(
            "Repaired clipped canonical_name" in rec.message
            for rec in caplog.records
        )

    def test_unrepairable_mismatch_logs_warning_but_keeps_name(
        self, caplog: "logging.LogCaptureFixture",
    ) -> None:
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {
            "entities": [
                {
                    "entity_type": "person",
                    "canonical_name": "Mxyzptlk",
                    "quote": "Just a quiet day at home.",
                    "confidence": 0.3,
                },
            ],
            "relationships": [],
        }
        mock = MagicMock()
        mock.content = [tool_block]
        with caplog.at_level(
            logging.WARNING, logger="journal.providers.extraction",
        ):
            result = _parse_tool_response(mock)

        # Name kept as-is — we never invent characters
        assert result.entities[0]["canonical_name"] == "Mxyzptlk"
        # But a warning is logged so it can be reviewed manually
        assert any(
            "does not appear" in rec.message for rec in caplog.records
        )
