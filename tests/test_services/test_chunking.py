"""Tests for text chunking."""

from journal.services.chunking import chunk_text, count_tokens, split_sentences


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


def test_short_text_single_chunk():
    chunks = chunk_text("This is a short journal entry.")
    assert len(chunks) == 1
    assert chunks[0] == "This is a short journal entry."


def test_empty_text():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_long_text_splits():
    # Create text longer than default 500 tokens
    paragraphs = [f"This is paragraph number {i}. " * 20 for i in range(10)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_text(text, max_tokens=100, overlap_tokens=20)
    assert len(chunks) > 1

    # Each chunk should be within the token limit (with some tolerance for boundaries)
    for chunk in chunks:
        tokens = count_tokens(chunk)
        assert tokens <= 150  # Allow some tolerance for paragraph boundaries


def test_overlap_between_chunks():
    # Create text that will be split into multiple chunks
    paragraphs = [f"Unique paragraph {i} with some content." for i in range(20)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_text(text, max_tokens=50, overlap_tokens=20)
    assert len(chunks) > 1

    # Check that consecutive chunks share some content (overlap)
    for i in range(len(chunks) - 1):
        # At least some words from the end of chunk i should appear in chunk i+1
        words_end = set(chunks[i].split()[-5:])
        words_start = set(chunks[i + 1].split()[:10])
        assert words_end & words_start, f"No overlap between chunk {i} and {i + 1}"


def test_preserves_paragraph_structure():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunk_text(text, max_tokens=1000)
    assert len(chunks) == 1
    assert "First paragraph." in chunks[0]
    assert "Second paragraph." in chunks[0]
    assert "Third paragraph." in chunks[0]
