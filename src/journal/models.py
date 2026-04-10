"""Shared data models."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Entry:
    id: int
    entry_date: str
    source_type: str
    raw_text: str
    final_text: str = ""
    word_count: int = 0
    chunk_count: int = 0
    language: str = "en"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class EntryPage:
    id: int
    entry_id: int
    page_number: int
    raw_text: str
    source_file_id: int | None = None
    created_at: str = ""


@dataclass
class MoodScore:
    entry_id: int
    dimension: str
    score: float
    confidence: float | None = None


@dataclass
class Statistics:
    total_entries: int
    date_range_start: str | None
    date_range_end: str | None
    total_words: int
    avg_words_per_entry: float
    entries_per_month: float


@dataclass
class MoodTrend:
    period: str
    dimension: str
    avg_score: float
    entry_count: int


@dataclass(frozen=True)
class ChunkSpan:
    """A chunk of text with its position in the source text and token count.

    `char_start` and `char_end` are character offsets into the original
    input text passed to `ChunkingStrategy.chunk()`. `char_end` is
    exclusive — `source_text[char_start:char_end]` yields the range the
    chunk covers in the source. That range may contain slightly more
    whitespace than `text` does, because paragraph and sentence
    separators are normalised when building the chunk's rendered text
    (paragraphs joined with `\\n\\n`, sentences with a single space).

    `token_count` is the tiktoken `cl100k_base` token count of `text`,
    which matches the tokenizer used by `text-embedding-3-large`.
    """

    text: str
    char_start: int
    char_end: int
    token_count: int


@dataclass
class ChunkMatch:
    """A single chunk that matched a query, with its relevance score."""

    text: str
    score: float


@dataclass
class SearchResult:
    """One entry's contribution to a search result set.

    `text` is the full parent entry (`final_text or raw_text`).
    `matching_chunks` lists every chunk in the entry that scored above
    the vector store's similarity cutoff, sorted by score descending.
    `score` is the top (max) chunk score — used to rank entries against
    each other in the result list.
    """

    entry_id: int
    entry_date: str
    text: str
    score: float
    matching_chunks: list[ChunkMatch] = field(default_factory=list)


@dataclass
class TopicFrequency:
    topic: str
    count: int
    entries: list[Entry] = field(default_factory=list)


EntityType = Literal["person", "place", "activity", "organization", "topic", "other"]


@dataclass
class Entity:
    id: int
    entity_type: EntityType
    canonical_name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    first_seen: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class EntityMention:
    id: int
    entity_id: int
    entry_id: int
    quote: str
    confidence: float
    extraction_run_id: str
    created_at: str = ""


@dataclass
class EntityRelationship:
    id: int
    subject_entity_id: int
    predicate: str
    object_entity_id: int
    quote: str
    entry_id: int
    confidence: float
    extraction_run_id: str
    created_at: str = ""


@dataclass
class ExtractionResult:
    entry_id: int
    extraction_run_id: str
    entities_created: int
    entities_matched: int
    mentions_created: int
    relationships_created: int
    warnings: list[str] = field(default_factory=list)
