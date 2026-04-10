"""Text chunking with tiktoken for embedding preparation.

The public entry point is the `ChunkingStrategy` Protocol. Concrete
implementations live in this module:

- `FixedTokenChunker` — paragraph-first packing with a tiktoken budget,
  sentence-level fallback for long paragraphs, and a fixed overlap.
  Deterministic, no external calls.
- `SemanticChunker` (added in WU-C) — embeds each sentence and cuts
  where adjacent similarity dips below a percentile threshold.

Use `build_chunker(config, embeddings)` to construct the right
implementation based on `config.chunking_strategy`.
"""

import logging
from typing import Protocol, runtime_checkable

import pysbd
import tiktoken

log = logging.getLogger(__name__)

_encoder = tiktoken.get_encoding("cl100k_base")

# pysbd.Segmenter is stateful but thread-safe for read-only use. Build it
# once at import time to amortise construction cost across many calls.
# clean=False preserves the user's exact whitespace and punctuation in the
# returned sentences — we don't want the segmenter "helpfully" normalising
# input that will later be compared against raw_text/final_text.
_segmenter = pysbd.Segmenter(language="en", clean=False)


def count_tokens(text: str) -> int:
    return len(_encoder.encode(text))


def split_sentences(text: str) -> list[str]:
    """Split text into sentences using pysbd.

    Handles abbreviations (Dr., a.m., i.e.), decimals ($3.14), ellipses,
    and em-dashes correctly — unlike a naive `.!?` scan. Returns an empty
    list for empty/whitespace-only input.
    """
    if not text or not text.strip():
        return []
    # pysbd can return empty strings or whitespace-only fragments for
    # pathological inputs; filter them out.
    return [s.strip() for s in _segmenter.segment(text) if s and s.strip()]


@runtime_checkable
class ChunkingStrategy(Protocol):
    """Protocol implemented by every chunker.

    Implementations turn a single block of text (a full journal entry)
    into a list of chunks ready to be embedded and stored.
    """

    def chunk(self, text: str) -> list[str]: ...


class FixedTokenChunker:
    """Paragraph-first chunker with a tiktoken budget and fixed overlap.

    Algorithm:
    1. If the whole text fits in `max_tokens`, return it as a single chunk.
    2. Otherwise split on blank lines into paragraphs and greedily pack
       them into chunks up to `max_tokens`. When a chunk is flushed,
       carry `overlap_tokens` worth of trailing paragraphs into the next
       chunk as context.
    3. If a single paragraph is longer than `max_tokens`, fall back to
       sentence-level packing (via `split_sentences`) within that
       paragraph.

    Deterministic, no external calls. Used as the default strategy and
    as a fallback for the max-size enforcement step of SemanticChunker.
    """

    def __init__(self, max_tokens: int = 150, overlap_tokens: int = 40) -> None:
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        # If the whole text fits in one chunk, return it directly.
        if count_tokens(text) <= self._max_tokens:
            return [text.strip()]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_tokens = 0

        for paragraph in paragraphs:
            para_tokens = count_tokens(paragraph)

            # If a single paragraph exceeds max_tokens, split by sentences.
            if para_tokens > self._max_tokens:
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = []
                    current_tokens = 0

                chunks.extend(
                    _split_long_paragraph(
                        paragraph, self._max_tokens, self._overlap_tokens
                    )
                )
                continue

            # Check if adding this paragraph would exceed the limit.
            if current_tokens + para_tokens > self._max_tokens and current_chunk:
                chunks.append("\n\n".join(current_chunk))

                # Carry trailing paragraphs as overlap into the next chunk.
                overlap_parts: list[str] = []
                overlap_count = 0
                for prev in reversed(current_chunk):
                    prev_tokens = count_tokens(prev)
                    if overlap_count + prev_tokens > self._overlap_tokens:
                        break
                    overlap_parts.insert(0, prev)
                    overlap_count += prev_tokens

                current_chunk = overlap_parts
                current_tokens = overlap_count

            current_chunk.append(paragraph)
            current_tokens += para_tokens

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        log.debug("FixedTokenChunker produced %d chunks", len(chunks))
        return chunks


def build_chunker(config, embeddings=None) -> ChunkingStrategy:
    """Factory: return the right chunker for the current config.

    `embeddings` is unused by FixedTokenChunker and required by
    SemanticChunker (WU-C). Accept it here as Optional so early callers
    don't have to plumb it through until semantic lands.
    """
    strategy = getattr(config, "chunking_strategy", "fixed")
    if strategy == "fixed":
        return FixedTokenChunker(
            max_tokens=config.chunking_max_tokens,
            overlap_tokens=config.chunking_overlap_tokens,
        )
    # SemanticChunker support is added in WU-C; for now fall back to fixed
    # and log a warning so misconfiguration is visible.
    log.warning(
        "Unknown chunking_strategy %r — falling back to FixedTokenChunker. "
        "SemanticChunker lands in WU-C.",
        strategy,
    )
    return FixedTokenChunker(
        max_tokens=config.chunking_max_tokens,
        overlap_tokens=config.chunking_overlap_tokens,
    )


def _split_long_paragraph(
    paragraph: str, max_tokens: int, overlap_tokens: int
) -> list[str]:
    """Split a long paragraph by sentences with overlap."""
    sentences = split_sentences(paragraph)
    if not sentences:
        return [paragraph]

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sent_tokens = count_tokens(sentence)

        if current_tokens + sent_tokens > max_tokens and current_chunk:
            chunks.append(" ".join(current_chunk))

            # Overlap
            overlap_parts: list[str] = []
            overlap_count = 0
            for prev in reversed(current_chunk):
                prev_tokens = count_tokens(prev)
                if overlap_count + prev_tokens > overlap_tokens:
                    break
                overlap_parts.insert(0, prev)
                overlap_count += prev_tokens

            current_chunk = overlap_parts
            current_tokens = overlap_count

        current_chunk.append(sentence)
        current_tokens += sent_tokens

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks
