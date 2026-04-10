"""OCR Protocol and Anthropic adapter.

The Anthropic adapter supports optional "context priming" via static
markdown files. When `context_dir` is provided, every file in that
directory is loaded once at construction time and concatenated into the
system prompt. The resulting block is marked `cache_control` so
Anthropic can cache it across requests — cache hits are ~12.5× cheaper
than re-sending the context uncached.

The context is intended for proper-noun glossaries: family names,
place names, recurring topics — things that improve OCR accuracy on
handwritten text. See `docs/ocr-context.md` for the design rationale,
risks, and recommended content.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import anthropic
import tiktoken

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an expert handwriting OCR system. Extract all text from the provided "
    "handwritten image as accurately as possible. Preserve paragraph breaks and line "
    "structure. Output only the extracted text with no commentary or preamble."
)

# Minimum tokens for a cacheable block on Claude Opus 4.6. Below this
# the Anthropic API silently ignores cache_control and bills the block
# as a normal input token for every request. The provider logs a
# warning if the composed system text is smaller than this.
CACHEABLE_MINIMUM_TOKENS = 4096

# Instructions that always ride alongside the glossary. These exist
# primarily to defend against the "hallucinated substitution" failure
# mode — the model replacing an ambiguous scribble with a glossary
# entry that isn't actually what was written.
CONTEXT_USAGE_INSTRUCTIONS = (
    "\n\nThe sections below contain proper nouns (people, places, topics) "
    "that appear frequently in this author's handwritten journal. Use them "
    "as a candidate list ONLY — prefer a glossary spelling when the "
    "handwritten token is visually consistent with the entry, but do NOT "
    "substitute for the sake of matching. If a word is ambiguous AND does "
    "not match any glossary entry, transcribe exactly what you see, even "
    "if it looks like a typo. Never invent a glossary match that isn't "
    "supported by the pen strokes on the page."
)


def load_context_files(context_dir: Path | None) -> str:
    """Load and concatenate all markdown files in the context directory.

    Returns an empty string if `context_dir` is None, doesn't exist, or
    contains no `.md` files. Files are read in alphabetical order (so
    the composed blob is deterministic across restarts) and each one is
    prefixed with a `# <filename>` header so the model can tell them
    apart when they overlap (e.g. someone named after a place).

    Reads are best-effort: if any individual file is unreadable the
    error is logged and that file is skipped rather than failing the
    whole server startup.
    """
    if context_dir is None:
        return ""
    if not context_dir.exists() or not context_dir.is_dir():
        logger.warning(
            "OCR context dir %s does not exist — skipping context priming",
            context_dir,
        )
        return ""

    files = sorted(context_dir.glob("*.md"))
    if not files:
        logger.info(
            "OCR context dir %s has no *.md files — skipping context priming",
            context_dir,
        )
        return ""

    parts: list[str] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning(
                "Failed to read OCR context file %s: %s — skipping", path, e
            )
            continue
        if not content:
            continue
        # Derive a heading from the filename stem so the model has a
        # category label for each section.
        heading = path.stem.replace("_", " ").replace("-", " ").strip()
        parts.append(f"# {heading}\n\n{content}")

    if not parts:
        return ""

    return "\n\n".join(parts)


def _build_cache_control(ttl: str) -> dict[str, str]:
    """Build a `cache_control` block from a TTL string.

    Anthropic supports two cache tiers: the default 5-minute ephemeral
    cache and an optional 1-hour cache. 1-hour is cheaper amortized
    when an ingestion session involves more than a handful of requests.
    """
    if ttl == "5m":
        return {"type": "ephemeral"}
    if ttl == "1h":
        return {"type": "ephemeral", "ttl": "1h"}
    raise ValueError(
        f"Invalid OCR context cache TTL {ttl!r} — must be '5m' or '1h'"
    )


@runtime_checkable
class OCRProvider(Protocol):
    """Protocol for OCR providers."""

    def extract_text(self, image_data: bytes, media_type: str) -> str: ...


class AnthropicOCRProvider:
    """OCR provider using Anthropic's Claude vision API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        context_dir: Path | None = None,
        cache_ttl: str = "5m",
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._cache_control = _build_cache_control(cache_ttl)

        # Compose the system text once at construction time. Startup is
        # the only time context files are read — restarting the server
        # is the intended way to reload context after editing it.
        context_text = load_context_files(context_dir)
        if context_text:
            self._system_text = (
                SYSTEM_PROMPT + CONTEXT_USAGE_INSTRUCTIONS + "\n\n" + context_text
            )
            logger.info(
                "OCR context loaded from %s (%d chars)",
                context_dir,
                len(context_text),
            )
        else:
            self._system_text = SYSTEM_PROMPT

        self._warn_if_below_cache_minimum()

    def _warn_if_below_cache_minimum(self) -> None:
        """Log a loud warning if the composed system text won't cache.

        Anthropic silently ignores cache_control on blocks below
        CACHEABLE_MINIMUM_TOKENS, so misconfigured context_dirs end up
        paying full per-request input cost with no user-visible error.
        The warning gives the user a single log line they can search
        for if their context-primed OCR becomes unexpectedly expensive.
        """
        try:
            encoder = tiktoken.encoding_for_model("gpt-4")
        except KeyError:
            encoder = tiktoken.get_encoding("cl100k_base")
        # cl100k_base is not Claude's actual tokenizer, but it is a
        # close-enough proxy for "is this block big enough to cache?".
        # Anthropic doesn't ship a Python tokenizer for Claude that
        # the server can use offline.
        token_count = len(encoder.encode(self._system_text))
        if token_count < CACHEABLE_MINIMUM_TOKENS:
            logger.warning(
                "OCR system text is %d tokens (approx) — below the %d-token "
                "cache minimum for %s. cache_control will be silently "
                "ignored and every request will pay full input price. "
                "Add more context files or increase their size to enable "
                "caching.",
                token_count,
                CACHEABLE_MINIMUM_TOKENS,
                self._model,
            )
        else:
            logger.info(
                "OCR system text is %d tokens — cache eligible on %s",
                token_count,
                self._model,
            )

    def extract_text(self, image_data: bytes, media_type: str) -> str:
        """Extract text from an image using Anthropic's vision API."""
        logger.info("Extracting text via Anthropic OCR (model=%s)", self._model)

        encoded_image = base64.standard_b64encode(image_data).decode("utf-8")

        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": self._system_text,
                    "cache_control": self._cache_control,
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract all handwritten text from this image.",
                        },
                    ],
                }
            ],
        )

        extracted = message.content[0].text
        logger.info("OCR extraction complete (%d characters)", len(extracted))
        return extracted
