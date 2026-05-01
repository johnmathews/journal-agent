"""Build transcription context strings from the OCR context files.

Two output formats are supported, one per provider class:

1. ``build_whisper_prompt`` — for OpenAI's transcription endpoint, which exposes
   only a `prompt` parameter (~224-token cap) used as a *spelling bias*. Output is
   markdown-stripped, whitespace-collapsed, and truncated to ~200 tokens.
2. ``build_full_context_instruction`` — for Gemini, which accepts a
   ``system_instruction`` of effectively unlimited length and treats it as a
   genuine instruction. Output preserves the full markdown structure so the model
   can read it as a glossary, with an anti-hallucination preamble.

A single set of context files (``OCR_CONTEXT_DIR``) drives both formats — no second
directory to maintain.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from journal.providers.ocr import load_context_files

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

# Whisper's documented prompt limit is 224 tokens. We aim for 200 to leave headroom
# against tokenizer drift between tiktoken's `o200k_base` and OpenAI's actual encoding.
DEFAULT_MAX_TOKENS = 200

# Markdown syntax to strip. We do not need a full Markdown parser here — context files
# are simple lists with bold names; line-based regex is sufficient. Leading-whitespace
# classes use `[ \t]*` rather than `\s*` so they cannot greedily swallow the preceding
# newline (which would collapse paragraph breaks).
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}\s+", flags=re.MULTILINE)
_LIST_BULLET_RE = re.compile(r"^[ \t]*[-*+]\s+", flags=re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_UNDERSCORE_EM_RE = re.compile(r"(?<!_)_([^_]+)_(?!_)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_HORIZONTAL_RULE_RE = re.compile(r"^[ \t]*-{3,}[ \t]*$", flags=re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """Strip common markdown syntax, keeping the inner text intact."""
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    text = _UNDERSCORE_EM_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _HEADING_RE.sub("", text)
    text = _LIST_BULLET_RE.sub("", text)
    text = _HORIZONTAL_RULE_RE.sub("", text)
    return text


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs (including newlines) into single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate *text* so its tokenization is at most *max_tokens* tokens.

    Falls back to a character-budget truncation if tiktoken is unavailable or fails on
    the input. The character-budget heuristic is intentionally conservative (3.5 chars
    per token) so we under-shoot rather than overshoot the API limit.
    """
    if not text:
        return text
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return enc.decode(tokens[:max_tokens])
    except Exception:
        log.warning(
            "tiktoken truncation failed — falling back to character budget",
            exc_info=True,
        )
        char_budget = max_tokens * 3
        if len(text) <= char_budget:
            return text
        return text[:char_budget]


def build_whisper_prompt(
    context_dir: Path | None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Build a Whisper `prompt` string from the OCR context directory.

    Returns an empty string when the directory is missing, empty, or contains no useful
    content. The OCR loader already handles missing-directory and read-error logging,
    so this function only needs to post-process the loaded text.
    """
    raw = load_context_files(context_dir)
    if not raw:
        return ""

    cleaned = _strip_markdown(raw)
    cleaned = _normalize_whitespace(cleaned)
    if not cleaned:
        return ""

    truncated = _truncate_to_tokens(cleaned, max_tokens)
    log.info(
        "Whisper transcription context built (%d chars, ~%d tokens)",
        len(truncated),
        max_tokens if len(truncated) < len(cleaned) else _approx_tokens(truncated),
    )
    return truncated


def _approx_tokens(text: str) -> int:
    """Best-effort token count for logging only."""
    try:
        import tiktoken

        return len(tiktoken.get_encoding("o200k_base").encode(text))
    except Exception:
        return max(1, len(text) // 4)


_FULL_CONTEXT_PREAMBLE = (
    "The following is a glossary of proper nouns, places, and recurring topics "
    "that may appear in the audio. Prefer these spellings when the spoken word "
    "is phonetically consistent with one of them. Do not invent words that are "
    "not actually heard — the glossary is a spelling reference, not a list of "
    "things you must include in the transcript."
)


def build_full_context_instruction(context_dir: Path | None) -> str:
    """Build a full-length system instruction for instruction-following providers.

    Unlike ``build_whisper_prompt``, this preserves the markdown structure of the
    context files (headings, bullets, bold names) so the model can read it as a
    structured glossary. There is no token cap — Gemini's system_instruction is
    effectively unlimited for our purposes.

    Returns an empty string when the directory is missing or empty so that the
    caller can decide whether to fall back to a default system prompt.
    """
    raw = load_context_files(context_dir)
    if not raw:
        return ""
    instruction = f"{_FULL_CONTEXT_PREAMBLE}\n\n{raw}"
    log.info(
        "Full-context transcription instruction built (%d chars, ~%d tokens)",
        len(instruction),
        _approx_tokens(instruction),
    )
    return instruction
