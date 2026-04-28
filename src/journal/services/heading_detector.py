"""Date-heading detector — promotes a leading date into a markdown heading."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Detector inspects only the leading slice of text — anything past this point can't be the
# heading anyway, and limiting input keeps LLM cost predictable.
_DETECTION_WINDOW_CHARS = 300

SYSTEM_PROMPT = """\
You normalize the start of a journal entry. Users often dictate or write a date (sometimes
followed by a time) at the very start, before describing their day.

Given text from the start of a journal entry, decide whether it begins with a date that
should be lifted into a markdown heading.

These COUNT as a heading:
- Numeric: "28 April 2026", "April 28th, 2026", "April 28th"
- Spelled-out: "the twenty-eighth of April two thousand and twenty-six"
- Relative: "today", "yesterday", "tomorrow"
- With a time: "28 April 2026, 9am" or "Today at 9.30am"

These do NOT count as a heading — return is_heading=false:
- A date that appears mid-sentence: "I went to Berlin on April 28th"
- Text that already starts with a markdown heading (`# ...`)
- Any text where the date is not the very first content

If entry_date is provided in the user message (ISO 8601), use it to resolve relative phrases
("today", "yesterday") into the canonical absolute form.

Respond with ONLY a JSON object on a single line, no other text, no markdown fences:

{"is_heading": true, "heading_text": "28 April 2026", "source_phrase": "April 28th. "}
{"is_heading": false, "heading_text": null, "source_phrase": null}

Where:
- heading_text: the canonical form to use as the heading (e.g. "28 April 2026" or
  "28 April 2026, 9am"). Use a clean, consistent format. No trailing punctuation.
  Day-month-year order. Lowercase the time-of-day suffix ("9am", not "9AM").
- source_phrase: the EXACT verbatim substring from the start of the input text that became
  the heading, INCLUDING any trailing punctuation and whitespace that should be stripped
  from the body. The remainder of the input AFTER source_phrase is the body.
  Example — input "April 28th. Today I went...", source_phrase is "April 28th. "
  (eleven characters plus a trailing space, total 12 chars).
"""


@dataclass(frozen=True)
class HeadingDetectionResult:
    """Outcome of running the heading detector on a piece of text.

    `heading_text` is the canonical heading form (e.g. ``"28 April 2026"``) when a heading
    was detected, otherwise the empty string. `body` is the remainder of the input — the
    text after the detected date phrase, with heading-area leading whitespace stripped.
    When no heading is detected, `body` is the original input verbatim.
    """

    heading_text: str
    body: str

    @property
    def has_heading(self) -> bool:
        return bool(self.heading_text)

    def to_text(self) -> str:
        """Recombine into a single markdown string."""
        if not self.has_heading:
            return self.body
        if self.body:
            return f"# {self.heading_text}\n\n{self.body}"
        return f"# {self.heading_text}\n"


@runtime_checkable
class HeadingDetector(Protocol):
    """Protocol for the date-heading detection step."""

    def detect(
        self, text: str, entry_date: str | None = None
    ) -> HeadingDetectionResult: ...


class NullHeadingDetector:
    """No-op detector — every input is returned with no heading."""

    def detect(
        self, text: str, entry_date: str | None = None
    ) -> HeadingDetectionResult:
        return HeadingDetectionResult(heading_text="", body=text)


class AnthropicHeadingDetector:
    """Lifts a leading date in *text* into a markdown heading using Anthropic Haiku.

    Fails safe — any error in the LLM call, response parsing, or sanity checks results
    in a no-heading result with the original text as the body. The pipelines that consume
    this detector therefore never need to wrap the call in their own try/except.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5",
        max_tokens: int = 256,
    ) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def detect(
        self, text: str, entry_date: str | None = None
    ) -> HeadingDetectionResult:
        # Empty / whitespace-only — nothing to do.
        if not text or not text.strip():
            return HeadingDetectionResult(heading_text="", body=text)
        # Already a heading — caller authored this on purpose.
        if text.lstrip().startswith("#"):
            return HeadingDetectionResult(heading_text="", body=text)

        # Drop leading whitespace before reasoning about the start of the entry.
        # We never want it back in the body either way.
        stripped = text.lstrip()
        window = stripped[:_DETECTION_WINDOW_CHARS]

        user_content = (
            f"entry_date: {entry_date}\n\n{window}" if entry_date else window
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = response.content[0].text.strip()
        except Exception:
            log.warning(
                "Heading detector API call failed — returning text unchanged",
                exc_info=True,
            )
            return HeadingDetectionResult(heading_text="", body=text)

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            log.warning("Heading detector returned no JSON object: %r", raw[:200])
            return HeadingDetectionResult(heading_text="", body=text)
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            log.warning("Heading detector returned invalid JSON: %r", raw[:200])
            return HeadingDetectionResult(heading_text="", body=text)

        if not payload.get("is_heading"):
            return HeadingDetectionResult(heading_text="", body=text)

        heading_text = payload.get("heading_text")
        source_phrase = payload.get("source_phrase")

        if not isinstance(heading_text, str) or not heading_text.strip():
            return HeadingDetectionResult(heading_text="", body=text)
        if not isinstance(source_phrase, str) or not source_phrase:
            return HeadingDetectionResult(heading_text="", body=text)

        # The source_phrase must be a verbatim prefix of the (lstripped) input. This is
        # the bulletproof check against the model hallucinating an offset.
        if not window.startswith(source_phrase):
            log.warning(
                "Heading detector source_phrase %r is not a prefix of input — refusing",
                source_phrase[:80],
            )
            return HeadingDetectionResult(heading_text="", body=text)

        body = stripped[len(source_phrase):].lstrip()
        return HeadingDetectionResult(
            heading_text=heading_text.strip(), body=body
        )
