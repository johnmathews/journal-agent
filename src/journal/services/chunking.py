"""Text chunking with tiktoken for embedding preparation.

The public entry point is the `ChunkingStrategy` Protocol. Concrete
implementations live in this module:

- `FixedTokenChunker` — paragraph-first packing with a tiktoken budget,
  sentence-level fallback for long paragraphs, and a fixed overlap.
  Deterministic, no external calls.
- `SemanticChunker` — embeds each sentence and cuts where adjacent
  similarity dips below a percentile threshold.

Both strategies return `list[ChunkSpan]` — each chunk carries its text
plus the character range it covers in the source input, so downstream
consumers (the webapp overlay, the backfill CLI) can render or index
chunks against the original text without re-running the chunker.

Use `build_chunker(config, embeddings)` to construct the right
implementation based on `config.chunking_strategy`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import pysbd
import tiktoken

from journal.models import ChunkSpan

if TYPE_CHECKING:
    from journal.providers.embeddings import EmbeddingsProvider

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
    return [s.strip() for s in _segmenter.segment(text) if s and s.strip()]


@dataclass(frozen=True)
class _Piece:
    """Internal: a paragraph or sentence with its position in the source text.

    `text` is the stripped content. `start` and `end` are character
    offsets into the original text that was passed to the splitter.
    `source_text[start:end]` equals `text`.
    """

    text: str
    start: int
    end: int


def _split_paragraphs_with_offsets(text: str) -> list[_Piece]:
    """Split `text` on blank-line boundaries, tracking source offsets.

    Mirrors `text.split("\\n\\n")` but returns `_Piece` records so each
    paragraph carries its character range in the source. `start` and
    `end` bracket the stripped paragraph content — leading and trailing
    whitespace within the raw paragraph are excluded so that
    `text[start:end]` equals the stripped content verbatim.
    """
    pieces: list[_Piece] = []
    pos = 0
    for raw in text.split("\n\n"):
        raw_len = len(raw)
        stripped = raw.strip()
        if stripped:
            lstrip_offset = len(raw) - len(raw.lstrip())
            start = pos + lstrip_offset
            end = start + len(stripped)
            pieces.append(_Piece(text=stripped, start=start, end=end))
        pos += raw_len + 2  # +2 for the "\n\n" separator
    return pieces


def _split_sentences_with_offsets(text: str) -> list[_Piece]:
    """Split `text` into sentences via pysbd, tracking source offsets.

    pysbd normalises sentences when returning them, so exact source
    positions are resolved by scanning forward through the original
    text for each stripped sentence. A sentence that cannot be located
    (rare — only if pysbd altered characters beyond whitespace) is
    positioned at the current cursor and logged as a warning so it does
    not silently corrupt the overlay.
    """
    if not text or not text.strip():
        return []
    pieces: list[_Piece] = []
    cursor = 0
    for raw in _segmenter.segment(text):
        if not raw or not raw.strip():
            continue
        stripped = raw.strip()
        idx = text.find(stripped, cursor)
        if idx == -1:
            log.warning(
                "Sentence not locatable in source text: %r", stripped[:40]
            )
            idx = cursor
        pieces.append(_Piece(text=stripped, start=idx, end=idx + len(stripped)))
        cursor = idx + len(stripped)
    return pieces


def _span_from_paragraphs(pieces: list[_Piece]) -> ChunkSpan:
    """Build a `ChunkSpan` from a non-empty list of paragraph pieces."""
    chunk_text = "\n\n".join(p.text for p in pieces)
    return ChunkSpan(
        text=chunk_text,
        char_start=pieces[0].start,
        char_end=pieces[-1].end,
        token_count=count_tokens(chunk_text),
    )


def _span_from_sentences(pieces: list[_Piece]) -> ChunkSpan:
    """Build a `ChunkSpan` from a non-empty list of sentence pieces."""
    chunk_text = " ".join(p.text for p in pieces)
    return ChunkSpan(
        text=chunk_text,
        char_start=pieces[0].start,
        char_end=pieces[-1].end,
        token_count=count_tokens(chunk_text),
    )


@runtime_checkable
class ChunkingStrategy(Protocol):
    """Protocol implemented by every chunker.

    Implementations turn a single block of text (a full journal entry)
    into a list of `ChunkSpan` records ready to be embedded and stored.
    """

    def chunk(self, text: str) -> list[ChunkSpan]: ...


class FixedTokenChunker:
    """Paragraph-first chunker with a tiktoken budget and fixed overlap.

    Algorithm:
    1. If the whole text fits in `max_tokens`, return it as a single chunk.
    2. Otherwise split on blank lines into paragraphs and greedily pack
       them into chunks up to `max_tokens`. When a chunk is flushed,
       carry `overlap_tokens` worth of trailing paragraphs into the next
       chunk as context.
    3. If a single paragraph is longer than `max_tokens`, fall back to
       sentence-level packing within that paragraph.

    Deterministic, no external calls. Used as the default strategy and
    as a fallback for the max-size enforcement step of SemanticChunker.
    """

    def __init__(self, max_tokens: int = 150, overlap_tokens: int = 40) -> None:
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens

    def chunk(self, text: str) -> list[ChunkSpan]:
        if not text.strip():
            return []

        pieces = _split_paragraphs_with_offsets(text)
        if not pieces:
            return []

        # If the whole text fits in one chunk, return it directly.
        total_tokens = count_tokens(text)
        if total_tokens <= self._max_tokens:
            return [_span_from_paragraphs(pieces)]

        chunks: list[ChunkSpan] = []
        current: list[_Piece] = []
        current_tokens = 0

        for piece in pieces:
            para_tokens = count_tokens(piece.text)

            # If a single paragraph exceeds max_tokens, flush what we have
            # and split that paragraph by sentences.
            if para_tokens > self._max_tokens:
                if current:
                    chunks.append(_span_from_paragraphs(current))
                    current = []
                    current_tokens = 0

                chunks.extend(
                    _split_long_paragraph(
                        piece, self._max_tokens, self._overlap_tokens
                    )
                )
                continue

            # Check if adding this paragraph would exceed the limit.
            if current_tokens + para_tokens > self._max_tokens and current:
                chunks.append(_span_from_paragraphs(current))

                # Carry trailing paragraphs as overlap into the next chunk.
                overlap_parts: list[_Piece] = []
                overlap_count = 0
                for prev in reversed(current):
                    prev_tokens = count_tokens(prev.text)
                    if overlap_count + prev_tokens > self._overlap_tokens:
                        break
                    overlap_parts.insert(0, prev)
                    overlap_count += prev_tokens

                current = overlap_parts
                current_tokens = overlap_count

            current.append(piece)
            current_tokens += para_tokens

        if current:
            chunks.append(_span_from_paragraphs(current))

        log.debug("FixedTokenChunker produced %d chunks", len(chunks))
        return chunks


class SemanticChunker:
    """Content-adaptive chunker that cuts where topic meaning shifts.

    Algorithm:
    1. Split the text into sentences via pysbd (tracking source offsets).
    2. Batch-embed every sentence through the configured
       `EmbeddingsProvider`.
    3. Compute adjacent-sentence cosine similarities using numpy.
    4. Use percentile thresholds to classify cut positions:
       - `boundary_percentile` (default 25): any adjacent similarity at
         or below this percentile is a cut.
       - `decisive_percentile` (default 10): cuts at or below this are
         "clean" — no tail overlap. Cuts between `decisive_percentile`
         and `boundary_percentile` are "weak" transition points; the
         boundary sentence is duplicated into the next chunk as a
         lead-in.
    5. Enforce a minimum chunk size by merging undersized segments into
       their nearest neighbour (prefer backwards).
    6. Enforce a maximum chunk size by sentence-level packing within
       any still-oversized segment.

    Short texts (1–2 sentences) short-circuit to a single chunk.
    Empty / whitespace-only text returns `[]`.

    Does one additional `embed_texts` call per ingested entry, batched.
    Cost on a typical ~30-sentence entry is negligible versus the OCR
    call that precedes it.
    """

    def __init__(
        self,
        embeddings: EmbeddingsProvider,
        max_tokens: int = 300,
        min_tokens: int = 30,
        boundary_percentile: int = 25,
        decisive_percentile: int = 10,
    ) -> None:
        if not 0 <= decisive_percentile <= boundary_percentile <= 100:
            raise ValueError(
                "Require 0 <= decisive_percentile <= boundary_percentile <= 100, "
                f"got decisive={decisive_percentile}, boundary={boundary_percentile}"
            )
        self._embeddings = embeddings
        self._max_tokens = max_tokens
        self._min_tokens = min_tokens
        self._boundary_percentile = boundary_percentile
        self._decisive_percentile = decisive_percentile

    def chunk(self, text: str) -> list[ChunkSpan]:
        sent_pieces = _split_sentences_with_offsets(text)
        if not sent_pieces:
            return []
        # Short texts bypass the whole pipeline.
        if len(sent_pieces) <= 2:
            return [_span_from_sentences(sent_pieces)]

        sentence_texts = [p.text for p in sent_pieces]

        # 1. Embed every sentence as one batch.
        sent_vectors = self._embeddings.embed_texts(sentence_texts)

        # 2. Adjacent cosine similarities — vectorised.
        vecs = np.asarray(sent_vectors, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        # Guard against zero-norm vectors (shouldn't happen with real
        # embeddings but we want a defined behaviour if it does).
        normed = vecs / np.maximum(norms, 1e-12)
        sims = (normed[:-1] * normed[1:]).sum(axis=1)  # shape: (n-1,)

        # 3. Percentile thresholds.
        boundary_threshold = float(np.percentile(sims, self._boundary_percentile))
        decisive_threshold = float(np.percentile(sims, self._decisive_percentile))

        # 4. Identify cut positions. A cut at index i means "break between
        # sentence i and sentence i+1".
        cuts: list[tuple[int, bool]] = []
        for i, sim in enumerate(sims):
            if sim <= boundary_threshold:
                is_decisive = sim <= decisive_threshold
                cuts.append((i, bool(is_decisive)))

        # 5. Build segments from cut positions, applying adaptive overlap
        # for weak cuts.
        segments = _segment_with_adaptive_overlap(sent_pieces, cuts)

        # 6. Enforce min size — merge undersized segments.
        segments = _merge_undersized(segments, self._min_tokens)

        # 7. Enforce max size — sentence-pack any segment still over budget.
        segments = _split_oversized(segments, self._max_tokens)

        # 8. Materialise each segment as a ChunkSpan.
        chunks = [_span_from_sentences(seg) for seg in segments if seg]
        log.debug(
            "SemanticChunker produced %d chunks from %d sentences "
            "(boundary=%.3f, decisive=%.3f)",
            len(chunks), len(sent_pieces), boundary_threshold, decisive_threshold,
        )
        return chunks


def _segment_with_adaptive_overlap(
    sentences: list[_Piece], cuts: list[tuple[int, bool]]
) -> list[list[_Piece]]:
    """Split `sentences` at the given cut positions.

    Each cut is `(index, is_decisive)`. A cut at index `i` means break
    between sentence `i` and sentence `i+1`. For weak cuts (is_decisive
    is False), the boundary sentence (sentence `i`) is duplicated into
    the beginning of the next segment as a transition lead-in. For
    decisive cuts, no duplication.
    """
    if not cuts:
        return [sentences[:]]

    cuts_sorted = sorted(cuts, key=lambda c: c[0])
    segments: list[list[_Piece]] = []
    start = 0
    pending_overlap: _Piece | None = None

    for cut_idx, is_decisive in cuts_sorted:
        seg = sentences[start : cut_idx + 1]
        if pending_overlap is not None:
            seg = [pending_overlap, *seg]
            pending_overlap = None
        segments.append(seg)
        start = cut_idx + 1
        if not is_decisive:
            pending_overlap = sentences[cut_idx]

    tail = sentences[start:]
    if pending_overlap is not None:
        tail = [pending_overlap, *tail]
    if tail:
        segments.append(tail)

    return segments


def _seg_tokens(seg: list[_Piece]) -> int:
    return count_tokens(" ".join(p.text for p in seg))


def _merge_undersized(
    segments: list[list[_Piece]], min_tokens: int
) -> list[list[_Piece]]:
    """Merge segments whose token count is below `min_tokens` into a neighbour.

    Prefers merging backwards (into the previous segment). Falls forward
    for the first segment if it's too small.
    """
    if not segments:
        return segments

    merged: list[list[_Piece]] = []
    for seg in segments:
        if merged and _seg_tokens(seg) < min_tokens:
            merged[-1].extend(seg)
        else:
            merged.append(seg[:])

    if len(merged) >= 2 and _seg_tokens(merged[0]) < min_tokens:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)

    return merged


def _split_oversized(
    segments: list[list[_Piece]], max_tokens: int
) -> list[list[_Piece]]:
    """Split any segment whose token count exceeds `max_tokens`.

    Greedy sentence packing within the segment — the sentence list is
    iterated once and accumulated into sub-segments up to the token
    budget. Does not attempt any inter-sentence overlap (SemanticChunker
    already expresses overlap at the semantic-cut level).
    """
    result: list[list[_Piece]] = []
    for seg in segments:
        if _seg_tokens(seg) <= max_tokens:
            result.append(seg)
            continue
        current: list[_Piece] = []
        current_tokens = 0
        for sentence in seg:
            sent_tokens = count_tokens(sentence.text)
            if current_tokens + sent_tokens > max_tokens and current:
                result.append(current)
                current = []
                current_tokens = 0
            current.append(sentence)
            current_tokens += sent_tokens
        if current:
            result.append(current)
    return result


def build_chunker(config, embeddings: EmbeddingsProvider | None = None) -> ChunkingStrategy:
    """Factory: return the right chunker for the current config.

    `embeddings` is unused by FixedTokenChunker and required by
    SemanticChunker. If the config selects `semantic` but no embeddings
    provider is supplied (e.g. the backfill-chunks CLI which
    intentionally skips embeddings), this falls back to FixedTokenChunker
    with a warning rather than raising.
    """
    strategy = getattr(config, "chunking_strategy", "fixed")
    if strategy == "semantic":
        if embeddings is None:
            log.warning(
                "CHUNKING_STRATEGY=semantic but no embeddings provider "
                "supplied — falling back to FixedTokenChunker."
            )
            return FixedTokenChunker(
                max_tokens=config.chunking_max_tokens,
                overlap_tokens=config.chunking_overlap_tokens,
            )
        return SemanticChunker(
            embeddings=embeddings,
            max_tokens=config.chunking_max_tokens,
            min_tokens=getattr(config, "chunking_min_tokens", 30),
            boundary_percentile=getattr(config, "chunking_boundary_percentile", 25),
            decisive_percentile=getattr(config, "chunking_decisive_percentile", 10),
        )
    if strategy != "fixed":
        log.warning(
            "Unknown chunking_strategy %r — falling back to FixedTokenChunker.",
            strategy,
        )
    return FixedTokenChunker(
        max_tokens=config.chunking_max_tokens,
        overlap_tokens=config.chunking_overlap_tokens,
    )


def _split_long_paragraph(
    paragraph: _Piece, max_tokens: int, overlap_tokens: int
) -> list[ChunkSpan]:
    """Split a long paragraph by sentences with overlap.

    Sentence offsets are resolved relative to the paragraph text, then
    shifted by the paragraph's `start` so each resulting `ChunkSpan`
    carries character offsets into the original source text.
    """
    rel_pieces = _split_sentences_with_offsets(paragraph.text)
    if not rel_pieces:
        return [
            ChunkSpan(
                text=paragraph.text,
                char_start=paragraph.start,
                char_end=paragraph.end,
                token_count=count_tokens(paragraph.text),
            )
        ]

    abs_pieces = [
        _Piece(
            text=p.text,
            start=paragraph.start + p.start,
            end=paragraph.start + p.end,
        )
        for p in rel_pieces
    ]

    chunks: list[ChunkSpan] = []
    current: list[_Piece] = []
    current_tokens = 0

    for sentence in abs_pieces:
        sent_tokens = count_tokens(sentence.text)

        if current_tokens + sent_tokens > max_tokens and current:
            chunks.append(_span_from_sentences(current))

            overlap_parts: list[_Piece] = []
            overlap_count = 0
            for prev in reversed(current):
                prev_tokens = count_tokens(prev.text)
                if overlap_count + prev_tokens > overlap_tokens:
                    break
                overlap_parts.insert(0, prev)
                overlap_count += prev_tokens

            current = overlap_parts
            current_tokens = overlap_count

        current.append(sentence)
        current_tokens += sent_tokens

    if current:
        chunks.append(_span_from_sentences(current))

    return chunks
