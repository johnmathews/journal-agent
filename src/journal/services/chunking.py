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

import numpy as np
import pysbd
import tiktoken

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


class SemanticChunker:
    """Content-adaptive chunker that cuts where topic meaning shifts.

    Algorithm:
    1. Split the text into sentences via `split_sentences` (pysbd).
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
    6. Enforce a maximum chunk size by falling back to
       `FixedTokenChunker` on any oversized segment.

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

    def chunk(self, text: str) -> list[str]:
        sentences = split_sentences(text)
        if not sentences:
            return []
        # Short texts bypass the whole pipeline.
        if len(sentences) <= 2:
            return [text.strip()]

        # 1. Embed every sentence as one batch.
        sent_vectors = self._embeddings.embed_texts(sentences)

        # 2. Adjacent cosine similarities — vectorised.
        vecs = np.asarray(sent_vectors, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        # Guard against zero-norm vectors (shouldn't happen with real
        # embeddings but we want a defined behaviour if it does).
        normed = vecs / np.maximum(norms, 1e-12)
        sims = (normed[:-1] * normed[1:]).sum(axis=1)  # shape: (n-1,)

        # 3. Percentile thresholds. np.percentile uses linear interpolation
        # by default which is fine for our small arrays.
        boundary_threshold = float(np.percentile(sims, self._boundary_percentile))
        decisive_threshold = float(np.percentile(sims, self._decisive_percentile))

        # 4. Identify cut positions. A cut at index i means "break between
        # sentence i and sentence i+1".
        cuts: list[tuple[int, bool]] = []  # (index, is_decisive)
        for i, sim in enumerate(sims):
            if sim <= boundary_threshold:
                is_decisive = sim <= decisive_threshold
                cuts.append((i, bool(is_decisive)))

        # 5. Build segments from cut positions, applying adaptive overlap
        # for weak cuts.
        segments = _segment_with_adaptive_overlap(sentences, cuts)

        # 6. Enforce min size — merge undersized segments.
        segments = _merge_undersized(segments, self._min_tokens)

        # 7. Enforce max size — fall back to fixed-token packing for any
        # segment that's still over budget.
        segments = _split_oversized(segments, self._max_tokens)

        # 8. Join each segment's sentences into a single chunk string.
        chunks = [" ".join(seg) for seg in segments if seg]
        log.debug(
            "SemanticChunker produced %d chunks from %d sentences "
            "(boundary=%.3f, decisive=%.3f)",
            len(chunks), len(sentences), boundary_threshold, decisive_threshold,
        )
        return chunks


def _segment_with_adaptive_overlap(
    sentences: list[str], cuts: list[tuple[int, bool]]
) -> list[list[str]]:
    """Split `sentences` at the given cut positions.

    Each cut is `(index, is_decisive)`. A cut at index `i` means break
    between sentence `i` and sentence `i+1`. For weak cuts (is_decisive
    is False), the boundary sentence (sentence `i`) is duplicated into
    the beginning of the next segment as a transition lead-in. For
    decisive cuts, no duplication.
    """
    if not cuts:
        return [sentences[:]]

    # Sort cuts by position and iterate.
    cuts_sorted = sorted(cuts, key=lambda c: c[0])
    segments: list[list[str]] = []
    start = 0
    pending_overlap: str | None = None

    for cut_idx, is_decisive in cuts_sorted:
        # Segment = sentences[start .. cut_idx+1) = everything up to and
        # including sentence cut_idx.
        seg = sentences[start : cut_idx + 1]
        if pending_overlap is not None:
            seg = [pending_overlap, *seg]
            pending_overlap = None
        segments.append(seg)
        start = cut_idx + 1
        if not is_decisive:
            # Weak cut — duplicate the boundary sentence into the next
            # segment as context.
            pending_overlap = sentences[cut_idx]

    # Trailing segment.
    tail = sentences[start:]
    if pending_overlap is not None:
        tail = [pending_overlap, *tail]
    if tail:
        segments.append(tail)

    return segments


def _merge_undersized(
    segments: list[list[str]], min_tokens: int
) -> list[list[str]]:
    """Merge segments whose token count is below `min_tokens` into a neighbour.

    Prefers merging backwards (into the previous segment). Falls forward
    for the first segment if it's too small.
    """
    if not segments:
        return segments

    def seg_tokens(seg: list[str]) -> int:
        return count_tokens(" ".join(seg))

    merged: list[list[str]] = []
    for seg in segments:
        if merged and seg_tokens(seg) < min_tokens:
            # Merge this segment into the previous one.
            merged[-1].extend(seg)
        else:
            merged.append(seg[:])

    # Handle the case where the first segment is too small and no previous
    # exists — fall forward by merging into the second.
    if len(merged) >= 2 and seg_tokens(merged[0]) < min_tokens:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)

    return merged


def _split_oversized(
    segments: list[list[str]], max_tokens: int
) -> list[list[str]]:
    """Split any segment whose token count exceeds `max_tokens`.

    Falls back to sentence-level token packing using the same greedy
    loop as `FixedTokenChunker._split_long_paragraph`. The split is
    performed directly on the segment's sentence list (not re-joined
    and re-split) to avoid re-running pysbd and to preserve the exact
    sentence boundaries.
    """
    def seg_tokens(seg: list[str]) -> int:
        return count_tokens(" ".join(seg))

    result: list[list[str]] = []
    for seg in segments:
        if seg_tokens(seg) <= max_tokens:
            result.append(seg)
            continue
        # Greedy pack sentences into sub-segments up to max_tokens.
        current: list[str] = []
        current_tokens = 0
        for sentence in seg:
            sent_tokens = count_tokens(sentence)
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
