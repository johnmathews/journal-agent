"""Tests for text chunking."""

import math

import pytest

from journal.models import ChunkSpan
from journal.services.chunking import (
    ChunkingStrategy,
    FixedTokenChunker,
    SemanticChunker,
    count_tokens,
    split_sentences,
)


class StubEmbeddings:
    """Deterministic stub `EmbeddingsProvider` for testing SemanticChunker.

    The caller pre-registers a mapping from sentence → vector via
    `.vectors`. When `embed_texts` is called, each sentence is looked up
    in the map; unknown sentences get a zero vector. This lets us make
    very precise assertions about where cuts land given a known similarity
    structure.
    """

    def __init__(self, dim: int = 4):
        self._dim = dim
        self.vectors: dict[str, list[float]] = {}
        self.embed_calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [self.vectors.get(t, [0.0] * self._dim) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        return self.vectors.get(query, [0.0] * self._dim)


class TestSplitSentences:
    def test_simple_two_sentences(self):
        assert split_sentences("Hello. World.") == ["Hello.", "World."]

    def test_abbreviation_not_split(self):
        # "Dr." should not end a sentence.
        result = split_sentences("Dr. Smith went home. It was late.")
        assert result == ["Dr. Smith went home.", "It was late."]

    def test_title_abbreviations(self):
        # Common titles shouldn't trigger sentence breaks.
        result = split_sentences("Mr. Brown and Mrs. Green visited. They stayed an hour.")
        assert len(result) == 2
        assert "Mr. Brown and Mrs. Green visited." in result[0]

    def test_decimal_number_not_split(self):
        # "3.14" shouldn't split.
        result = split_sentences("The price was $3.14 today. Yesterday it was more.")
        assert len(result) == 2
        assert "$3.14" in result[0]

    def test_time_notation_not_split(self):
        # "a.m." shouldn't split.
        result = split_sentences("We left at 7 a.m. and arrived by noon.")
        assert result == ["We left at 7 a.m. and arrived by noon."]

    def test_latin_abbreviation_not_split(self):
        # "i.e." shouldn't split.
        result = split_sentences("He meant the bank, i.e. the riverbank. Not the building.")
        assert len(result) == 2

    def test_ellipsis_preserved(self):
        # Ellipsis should be preserved (not shattered into fragments). pysbd
        # treats "So..." as its own short sentence, which is fine — the
        # important thing is that "..." survives intact and doesn't split
        # into empty tokens.
        result = split_sentences("So... I started writing. It felt good.")
        assert len(result) == 3
        assert "..." in result[0]
        assert "" not in result  # no empty fragments

    def test_em_dash_within_sentence(self):
        # Em-dash is intra-sentence, not a boundary.
        text = "Life is good — simultaneously hard and easy — most of the time."
        result = split_sentences(text)
        assert len(result) == 1
        assert "—" in result[0]

    def test_exclamation_and_question(self):
        result = split_sentences("Really? Yes! Absolutely.")
        assert result == ["Really?", "Yes!", "Absolutely."]

    def test_empty_string(self):
        assert split_sentences("") == []

    def test_whitespace_only(self):
        assert split_sentences("   \n\t  ") == []

    def test_single_sentence_no_trailing_punct(self):
        result = split_sentences("This has no trailing punctuation")
        assert result == ["This has no trailing punctuation"]

    def test_multi_paragraph_prose(self):
        text = (
            "Sunday morning. I decided to journal again. "
            "It's been 10 days since I made that decision.\n\n"
            "I remember journaling as a kid. It felt like a superpower."
        )
        result = split_sentences(text)
        # Should produce 5 sentences, respecting periods but not paragraph breaks.
        assert len(result) == 5



def test_count_tokens():
    count = count_tokens("Hello world")
    assert count > 0


class TestFixedTokenChunker:
    def test_short_text_single_chunk(self):
        chunker = FixedTokenChunker()
        text = "This is a short journal entry."
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert chunks[0].text == text
        # Offsets must slice back to the chunk text for a single-paragraph input.
        assert text[chunks[0].char_start : chunks[0].char_end] == text
        assert chunks[0].token_count == count_tokens(text)

    def test_empty_text(self):
        chunker = FixedTokenChunker()
        assert chunker.chunk("") == []
        assert chunker.chunk("   ") == []

    def test_long_text_splits(self):
        paragraphs = [f"This is paragraph number {i}. " * 20 for i in range(10)]
        text = "\n\n".join(paragraphs)

        chunker = FixedTokenChunker(max_tokens=100, overlap_tokens=20)
        chunks = chunker.chunk(text)
        assert len(chunks) > 1

        # Each chunk should be within the token limit (tolerance for boundaries).
        for chunk in chunks:
            assert isinstance(chunk, ChunkSpan)
            assert chunk.token_count <= 150

    def test_overlap_between_chunks(self):
        paragraphs = [f"Unique paragraph {i} with some content." for i in range(20)]
        text = "\n\n".join(paragraphs)

        chunker = FixedTokenChunker(max_tokens=50, overlap_tokens=20)
        chunks = chunker.chunk(text)
        assert len(chunks) > 1

        # Consecutive chunks should share some content (overlap).
        for i in range(len(chunks) - 1):
            words_end = set(chunks[i].text.split()[-5:])
            words_start = set(chunks[i + 1].text.split()[:10])
            assert words_end & words_start, f"No overlap between chunk {i} and {i + 1}"

    def test_preserves_paragraph_structure(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunker = FixedTokenChunker(max_tokens=1000)
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert "First paragraph." in chunks[0].text
        assert "Second paragraph." in chunks[0].text
        assert "Third paragraph." in chunks[0].text

    def test_default_params(self):
        # Default 150/40 should produce one chunk for short text.
        chunker = FixedTokenChunker()
        assert len(chunker.chunk("Hello.")) == 1

    def test_implements_protocol(self):
        # Duck-typing check: FixedTokenChunker satisfies ChunkingStrategy.
        chunker = FixedTokenChunker()
        assert isinstance(chunker, ChunkingStrategy)

    def test_offsets_slice_back_to_chunk_text_single_paragraph(self):
        """For a single-paragraph chunk the source slice must equal chunk.text."""
        chunker = FixedTokenChunker(max_tokens=1000)
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        # Multi-paragraph chunk: the source range spans all three paragraphs
        # including the original `\n\n` separators.
        assert chunks[0].char_start == 0
        assert chunks[0].char_end == len(text)

    def test_offsets_non_overlapping_chunks_span_source(self):
        """Concatenated source ranges should cover the original paragraph positions."""
        paragraphs = [f"Paragraph {i} has meaningful content." for i in range(15)]
        text = "\n\n".join(paragraphs)

        chunker = FixedTokenChunker(max_tokens=40, overlap_tokens=0)
        chunks = chunker.chunk(text)
        assert len(chunks) > 1
        # First chunk must start at the start of the first paragraph, last
        # chunk must end at the end of the last paragraph.
        assert chunks[0].char_start == 0
        assert chunks[-1].char_end == len(text)
        # char_start is monotonically non-decreasing across chunks.
        for i in range(len(chunks) - 1):
            assert chunks[i].char_start <= chunks[i + 1].char_start

    def test_offsets_survive_leading_whitespace(self):
        """Leading blank lines must not shift paragraph offsets off by one."""
        text = "\n\n  First para.\n\nSecond para."
        chunker = FixedTokenChunker(max_tokens=1000)
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        # The first paragraph content ("First para.") starts after the
        # leading "\n\n  " — offset 4, not 0.
        first_para_end = chunks[0].char_start + len("First para.")
        assert text[chunks[0].char_start : first_para_end] == "First para."


def _unit_vector(angle: float, dim: int = 4) -> list[float]:
    """Build a unit vector pointing at `angle` radians in the xy-plane.

    The remaining dimensions are zero so cosine similarity between two
    such vectors is just cos(angle_diff) — a simple, human-predictable way
    to control test embeddings.
    """
    v = [0.0] * dim
    v[0] = math.cos(angle)
    v[1] = math.sin(angle)
    return v


class TestSemanticChunker:
    def test_implements_protocol(self):
        chunker = SemanticChunker(embeddings=StubEmbeddings())
        assert isinstance(chunker, ChunkingStrategy)

    def test_empty_text_returns_empty(self):
        chunker = SemanticChunker(embeddings=StubEmbeddings())
        assert chunker.chunk("") == []
        assert chunker.chunk("   ") == []

    def test_single_sentence_short_circuits(self):
        stub = StubEmbeddings()
        chunker = SemanticChunker(embeddings=stub)
        result = chunker.chunk("Just one sentence.")
        assert len(result) == 1
        assert result[0].text == "Just one sentence."
        # Short-circuit should not call the embedder.
        assert stub.embed_calls == []

    def test_two_sentences_short_circuit(self):
        stub = StubEmbeddings()
        chunker = SemanticChunker(embeddings=stub)
        result = chunker.chunk("First. Second.")
        assert len(result) == 1
        assert stub.embed_calls == []

    def test_three_sentences_with_one_decisive_cut(self):
        # s1 and s2 point the same direction; s3 is orthogonal.
        # That's a decisive cut between s2 and s3.
        stub = StubEmbeddings()
        s1 = "Vienna was beautiful in spring."
        s2 = "The blossoms were everywhere in Vienna."
        s3 = "I also need to call the dentist."
        stub.vectors[s1] = _unit_vector(0.0)  # (1, 0, 0, 0)
        stub.vectors[s2] = _unit_vector(0.1)  # nearly same direction
        stub.vectors[s3] = _unit_vector(math.pi / 2)  # orthogonal

        chunker = SemanticChunker(
            embeddings=stub,
            min_tokens=1,  # don't merge
            max_tokens=1000,  # don't split
            boundary_percentile=50,  # aggressive enough to cut
            decisive_percentile=50,  # any cut is decisive (no overlap)
        )
        result = chunker.chunk(f"{s1} {s2} {s3}")

        assert len(result) == 2
        assert s1 in result[0].text
        assert s2 in result[0].text
        assert s3 in result[1].text
        # Decisive cut — no overlap, s2 should only appear in chunk 0.
        assert s2 not in result[1].text

    def test_weak_cut_duplicates_boundary_sentence(self):
        # 5 sentences, 4 adjacent similarities engineered so that the
        # percentile buckets cleanly separate a weak cut from a decisive
        # cut. Similarity structure:
        #   sim[0] = 1.0  (s1↔s2 — stay together)
        #   sim[1] = 0.7  (s2↔s3 — WEAK cut, duplicate s2 into next chunk)
        #   sim[2] = 0.2  (s3↔s4 — DECISIVE cut, no overlap)
        #   sim[3] = 1.0  (s4↔s5 — stay together)
        #
        # With boundary_percentile=50 the threshold sits around 0.85, so
        # both the 0.7 and 0.2 sims fire as cuts. With decisive_percentile=25
        # the decisive threshold is around 0.575, so only the 0.2 sim is
        # decisive — the 0.7 sim is a weak cut.
        stub = StubEmbeddings()
        s1 = "Walking through the park this morning was lovely."
        s2 = "The cherry blossoms were out in force."
        s3 = "Life is good, mostly, these days."
        s4 = "Tomorrow I need to finish the quarterly report."
        s5 = "Deadlines at work are piling up again."
        stub.vectors[s1] = _unit_vector(0.0)
        stub.vectors[s2] = _unit_vector(0.0)  # sim(s1,s2)=1.0
        stub.vectors[s3] = _unit_vector(math.acos(0.7))  # sim(s2,s3)=0.7
        stub.vectors[s4] = _unit_vector(math.acos(0.2) + math.acos(0.7))  # sim(s3,s4)=0.2
        stub.vectors[s5] = _unit_vector(math.acos(0.2) + math.acos(0.7))  # sim(s4,s5)=1.0

        chunker = SemanticChunker(
            embeddings=stub,
            min_tokens=1,
            max_tokens=1000,
            boundary_percentile=50,
            decisive_percentile=25,
        )
        result = chunker.chunk(f"{s1} {s2} {s3} {s4} {s5}")

        # Expected segmentation:
        #   chunk 0: [s1, s2]                            (first run, ends before weak cut)
        #   chunk 1: [s2 (overlap), s3]                  (weak cut carries s2 forward)
        #   chunk 2: [s4, s5]                            (decisive cut, no overlap of s3)
        assert len(result) == 3
        assert s1 in result[0].text and s2 in result[0].text
        # Weak-cut overlap: s2 appears in BOTH chunk 0 and chunk 1.
        assert s2 in result[1].text
        assert s3 in result[1].text
        # Decisive cut: s3 must NOT appear in chunk 2.
        assert s3 not in result[2].text
        assert s4 in result[2].text and s5 in result[2].text

    def test_decisive_cut_does_not_overlap(self):
        # Same 5-sentence setup as the weak-cut test but with
        # decisive_percentile bumped to 50 so every cut is decisive.
        stub = StubEmbeddings()
        s1 = "Coffee with Atlas at the new cafe on Main Street today."
        s2 = "He showed me his latest drawings — they are wonderful."
        s3 = "Life is good, mostly, these days."
        s4 = "Tomorrow I must finish the tax return before the deadline."
        s5 = "Numbers and paperwork are not my favourite thing."
        stub.vectors[s1] = _unit_vector(0.0)
        stub.vectors[s2] = _unit_vector(0.0)
        stub.vectors[s3] = _unit_vector(math.acos(0.7))
        stub.vectors[s4] = _unit_vector(math.acos(0.2) + math.acos(0.7))
        stub.vectors[s5] = _unit_vector(math.acos(0.2) + math.acos(0.7))

        chunker = SemanticChunker(
            embeddings=stub,
            min_tokens=1,
            max_tokens=1000,
            boundary_percentile=50,
            decisive_percentile=50,  # every cut is decisive
        )
        result = chunker.chunk(f"{s1} {s2} {s3} {s4} {s5}")

        # Expected segmentation (all cuts decisive → no overlaps):
        #   chunk 0: [s1, s2]
        #   chunk 1: [s3]
        #   chunk 2: [s4, s5]
        assert len(result) == 3
        assert s1 in result[0].text and s2 in result[0].text
        assert s3 in result[1].text
        # No weak-cut overlap: s2 must NOT appear in chunk 1, s3 not in chunk 2.
        assert s2 not in result[1].text
        assert s3 not in result[2].text
        assert s4 in result[2].text and s5 in result[2].text

    def test_max_size_enforcement_splits_oversized_segment(self):
        # Build ~10 sentences that all point in the same direction so no
        # cuts happen from the semantic pass, then set a small max_tokens
        # so the single segment has to be broken by the fixed-size fallback.
        stub = StubEmbeddings()
        sentences = [f"Sentence number {i} with some padding words." for i in range(10)]
        for s in sentences:
            stub.vectors[s] = _unit_vector(0.0)
        text = " ".join(sentences)

        chunker = SemanticChunker(
            embeddings=stub,
            min_tokens=1,
            max_tokens=20,  # very small — forces sub-segmentation
            boundary_percentile=0,  # no semantic cuts
            decisive_percentile=0,
        )
        result = chunker.chunk(text)

        # Should produce multiple chunks via the oversize-split fallback.
        assert len(result) > 1
        for chunk in result:
            # Each sub-segment should fit roughly within max_tokens. Allow
            # some tolerance for a single long sentence that exceeds
            # max_tokens on its own (the packer will still emit it).
            assert chunk.token_count <= 40

    def test_min_size_enforcement_merges_tiny_segments(self):
        # 4 sentences, cut aggressively between every pair, but a min
        # token floor high enough that some segments need merging.
        stub = StubEmbeddings()
        sentences = [
            "Short one here.",
            "Another brief thought.",
            "And a third note.",
            "Finally the fourth bit.",
        ]
        angles = [0.0, math.pi / 2, math.pi, 3 * math.pi / 2]
        for s, a in zip(sentences, angles, strict=True):
            stub.vectors[s] = _unit_vector(a)

        chunker = SemanticChunker(
            embeddings=stub,
            min_tokens=20,  # each single sentence is too small alone
            max_tokens=1000,
            boundary_percentile=100,  # cut between every pair
            decisive_percentile=100,
        )
        result = chunker.chunk(" ".join(sentences))

        # After merging, we should have fewer chunks than cuts would suggest.
        assert 1 <= len(result) <= 3

    def test_raises_on_invalid_percentile_order(self):
        with pytest.raises(ValueError):
            SemanticChunker(
                embeddings=StubEmbeddings(),
                boundary_percentile=10,
                decisive_percentile=25,  # > boundary, invalid
            )

    def test_propagates_embedding_errors(self):
        class BrokenEmbedder:
            def embed_texts(self, texts):
                raise RuntimeError("embed exploded")

            def embed_query(self, q):
                return [0.0]

        chunker = SemanticChunker(embeddings=BrokenEmbedder())
        with pytest.raises(RuntimeError, match="embed exploded"):
            chunker.chunk("First sentence. Second sentence. Third sentence.")

    def test_batches_all_sentences_in_one_embed_call(self):
        stub = StubEmbeddings()
        sentences = ["S1.", "S2.", "S3.", "S4.", "S5."]
        for i, s in enumerate(sentences):
            stub.vectors[s] = _unit_vector(i * 0.1)

        chunker = SemanticChunker(embeddings=stub)
        chunker.chunk(" ".join(sentences))

        # Exactly one batched call to embed_texts.
        assert len(stub.embed_calls) == 1
        assert len(stub.embed_calls[0]) == 5

    def test_offsets_locate_sentences_in_source(self):
        """Each ChunkSpan's source range must contain the sentences it reports."""
        stub = StubEmbeddings()
        s1 = "Vienna was beautiful in spring today."
        s2 = "The blossoms were everywhere along the river."
        s3 = "I also need to call the dentist tomorrow."
        stub.vectors[s1] = _unit_vector(0.0)
        stub.vectors[s2] = _unit_vector(0.05)
        stub.vectors[s3] = _unit_vector(math.pi / 2)

        source = f"{s1} {s2} {s3}"
        chunker = SemanticChunker(
            embeddings=stub,
            min_tokens=1,
            max_tokens=1000,
            boundary_percentile=50,
            decisive_percentile=50,
        )
        result = chunker.chunk(source)

        assert len(result) >= 2
        # For every chunk, its source range must contain all the sentences
        # that appear in its text.
        for chunk in result:
            source_slice = source[chunk.char_start : chunk.char_end]
            for sentence in (s1, s2, s3):
                if sentence in chunk.text:
                    assert sentence in source_slice, (
                        f"chunk at [{chunk.char_start}:{chunk.char_end}] claims to contain "
                        f"{sentence!r} but source_slice is {source_slice!r}"
                    )
