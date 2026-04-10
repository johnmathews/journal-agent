"""Entity extraction Protocol and Anthropic adapter.

The adapter asks Claude to identify named entities and relationships
in a journal entry via the tool-use API, which forces structured JSON
output. The system prompt enumerates the supported entity types,
provides a preferred-predicate list for relationships, and tells the
model the author's name so first-person statements ("I visited Blue
Bottle") can be turned into ("<author>", "visited", "Blue Bottle")
triples.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import anthropic

logger = logging.getLogger(__name__)


ENTITY_TYPES = (
    "person",
    "place",
    "activity",
    "organization",
    "topic",
    "other",
)

PREFERRED_PREDICATES = (
    "at",
    "visited",
    "works_for",
    "knows",
    "plays",
    "attended",
    "mentioned",
    "part_of",
    "located_in",
)


def build_system_prompt(author_name: str) -> str:
    """Compose the extraction system prompt for a specific author.

    The author's name is inlined so the model can use it as the
    subject of first-person relationships. The prompt is conservative
    on purpose — extracting noise is worse than missing signal.
    """
    return (
        "You are an information extraction system for a personal journal.\n"
        "Given a single journal entry, identify named entities and the\n"
        "relationships between them.\n\n"
        f"The journal's author is named {author_name}. First-person\n"
        "actions (\"I went to the gym\", \"I played squash\") should be\n"
        f"recorded as relationships where the subject is {author_name!r}.\n"
        "If the author is not already in the entity list, add them as a\n"
        "'person' entity with canonical_name exactly equal to the author\n"
        "name above.\n\n"
        "Entity types (pick the best fit for each):\n"
        "  - person: a named individual, pet, or character\n"
        "  - place: a city, venue, building, region, or address\n"
        "  - activity: a verb-ish noun (squash, climbing, journaling)\n"
        "  - organization: a company, club, team, or institution\n"
        "  - topic: a subject or concept the author is thinking about\n"
        "  - other: only when none of the above fit\n\n"
        "Preferred predicates for relationships (use free text when none\n"
        "of these fit):\n"
        "  " + ", ".join(PREFERRED_PREDICATES) + "\n\n"
        "Rules:\n"
        "  - Be conservative. Only extract named or strongly-implied\n"
        "    entities. Do NOT invent generic nouns (e.g. 'the meeting').\n"
        "  - Every entity needs a verbatim quote from the entry that\n"
        "    supports the mention.\n"
        "  - Every relationship needs both subject and object to appear\n"
        "    as entities in the same response.\n"
        "  - If nothing is found, return empty arrays for entities and\n"
        "    relationships.\n"
        "  - Confidence is a number in [0.0, 1.0]: 1.0 = completely\n"
        "    certain, 0.5 = plausible guess, <0.3 = probably skip.\n\n"
        "Call the `record_entities` tool exactly once with your findings."
    )


ENTITY_EXTRACTION_TOOL: dict[str, Any] = {
    "name": "record_entities",
    "description": (
        "Record every named entity and relationship found in the entry."
    ),
    "input_schema": {
        "type": "object",
        "required": ["entities", "relationships"],
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "entity_type",
                        "canonical_name",
                        "quote",
                        "confidence",
                    ],
                    "properties": {
                        "entity_type": {
                            "type": "string",
                            "enum": list(ENTITY_TYPES),
                        },
                        "canonical_name": {"type": "string"},
                        "description": {"type": "string"},
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "quote": {"type": "string"},
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                },
            },
            "relationships": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "subject",
                        "predicate",
                        "object",
                        "quote",
                        "confidence",
                    ],
                    "properties": {
                        "subject": {"type": "string"},
                        "predicate": {"type": "string"},
                        "object": {"type": "string"},
                        "quote": {"type": "string"},
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                },
            },
        },
    },
}


@dataclass
class RawExtractionResult:
    """Unprocessed output from an extraction provider.

    `entities` and `relationships` are lists of dicts matching the keys
    in the tool schema. Normalisation, dedup, and persistence happen in
    `EntityExtractionService`.
    """

    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class ExtractionProvider(Protocol):
    """Protocol for entity extraction providers."""

    def extract_entities(
        self,
        entry_text: str,
        entry_date: str,
        author_name: str,
    ) -> RawExtractionResult: ...


class AnthropicExtractionProvider:
    """Extraction provider using Anthropic's tool-use API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def extract_entities(
        self,
        entry_text: str,
        entry_date: str,
        author_name: str,
    ) -> RawExtractionResult:
        """Call Claude to extract entities and relationships.

        The tool_choice parameter forces the model to return its answer
        as a tool call rather than prose, so we can parse
        `message.content[0].input` as a structured dict.
        """
        logger.info(
            "Extracting entities via Anthropic (model=%s, date=%s, chars=%d)",
            self._model,
            entry_date,
            len(entry_text),
        )

        system_text = build_system_prompt(author_name)
        user_text = (
            f"Entry date: {entry_date}\n\n"
            f"Entry text:\n{entry_text}"
        )

        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[ENTITY_EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "record_entities"},
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": user_text}],
                }
            ],
        )

        return _parse_tool_response(message)


def _parse_tool_response(message: Any) -> RawExtractionResult:
    """Extract entities/relationships from an Anthropic tool-use response.

    Handles minor defensive cases so a malformed or empty response
    produces an empty `RawExtractionResult` instead of crashing the
    batch. The tool_choice parameter should guarantee a `tool_use`
    block, but we still scan the whole content list for robustness.
    """
    if message is None:
        return RawExtractionResult()

    content = getattr(message, "content", None)
    if not content:
        return RawExtractionResult()

    tool_block: Any = None
    for block in content:
        if getattr(block, "type", None) == "tool_use":
            tool_block = block
            break
    if tool_block is None:
        # Fall back to the first block — FastMCP test stubs sometimes
        # just set `.input` on a MagicMock without a `type` attribute.
        tool_block = content[0]

    payload = getattr(tool_block, "input", None) or {}
    entities_raw = payload.get("entities") or []
    relationships_raw = payload.get("relationships") or []

    entities: list[dict[str, Any]] = []
    for item in entities_raw:
        if not isinstance(item, dict):
            continue
        entities.append(
            {
                "entity_type": item.get("entity_type", "other"),
                "canonical_name": item.get("canonical_name", "").strip(),
                "description": item.get("description", "") or "",
                "aliases": list(item.get("aliases") or []),
                "quote": item.get("quote", "") or "",
                "confidence": float(item.get("confidence", 0.0) or 0.0),
            }
        )

    relationships: list[dict[str, Any]] = []
    for item in relationships_raw:
        if not isinstance(item, dict):
            continue
        relationships.append(
            {
                "subject": item.get("subject", "").strip(),
                "predicate": item.get("predicate", "").strip(),
                "object": item.get("object", "").strip(),
                "quote": item.get("quote", "") or "",
                "confidence": float(item.get("confidence", 0.0) or 0.0),
            }
        )

    return RawExtractionResult(entities=entities, relationships=relationships)
