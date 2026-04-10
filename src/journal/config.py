"""Configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # Database
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("DB_PATH", "journal.db")))

    # ChromaDB
    chromadb_host: str = field(default_factory=lambda: os.environ.get("CHROMADB_HOST", "localhost"))
    chromadb_port: int = field(
        default_factory=lambda: int(os.environ.get("CHROMADB_PORT", "8000"))
    )
    chromadb_collection: str = "journal_entries"

    # Anthropic (OCR)
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    ocr_model: str = "claude-opus-4-6"
    ocr_max_tokens: int = 4096

    # OpenAI (Whisper + Embeddings)
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    transcription_model: str = "gpt-4o-transcribe"
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 1024

    # Slack (for downloading files from Slack URLs)
    slack_bot_token: str = field(
        default_factory=lambda: os.environ.get("SLACK_BOT_TOKEN", "")
    )

    # Chunking
    chunking_strategy: str = field(
        default_factory=lambda: os.environ.get("CHUNKING_STRATEGY", "semantic")
    )
    chunking_max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("CHUNKING_MAX_TOKENS", "150"))
    )
    chunking_overlap_tokens: int = field(
        default_factory=lambda: int(os.environ.get("CHUNKING_OVERLAP_TOKENS", "40"))
    )
    # SemanticChunker only — min chunk size in tokens. Segments below this
    # are merged with their nearest neighbour.
    chunking_min_tokens: int = field(
        default_factory=lambda: int(os.environ.get("CHUNKING_MIN_TOKENS", "30"))
    )
    # SemanticChunker only — percentile (0-100) at or below which adjacent
    # sentence similarity counts as a chunk boundary. Smaller = more
    # conservative (fewer cuts, larger chunks). Larger = more aggressive.
    chunking_boundary_percentile: int = field(
        default_factory=lambda: int(os.environ.get("CHUNKING_BOUNDARY_PERCENTILE", "25"))
    )
    # SemanticChunker only — percentile below which a cut is considered
    # "decisive" and no tail overlap is carried. Cuts between
    # decisive_percentile and boundary_percentile are "weak" cuts that
    # duplicate the boundary sentence into both adjacent chunks as
    # transition context.
    chunking_decisive_percentile: int = field(
        default_factory=lambda: int(os.environ.get("CHUNKING_DECISIVE_PERCENTILE", "10"))
    )
    # If true, prepend a "Date: YYYY-MM-DD. Weekday." header to each chunk
    # before embedding (but store the un-prefixed chunk as the ChromaDB
    # document). Helps date-sensitive queries retrieve the right entries.
    chunking_embed_metadata_prefix: bool = field(
        default_factory=lambda: os.environ.get(
            "CHUNKING_EMBED_METADATA_PREFIX", "true"
        ).lower() in ("1", "true", "yes", "on")
    )

    # MCP Server
    mcp_host: str = field(default_factory=lambda: os.environ.get("MCP_HOST", "0.0.0.0"))
    mcp_port: int = field(default_factory=lambda: int(os.environ.get("MCP_PORT", "8000")))
    mcp_allowed_hosts: list[str] = field(
        default_factory=lambda: [
            h.strip()
            for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        ]
    )

    # REST API CORS
    api_cors_origins: list[str] = field(
        default_factory=lambda: [
            h.strip()
            for h in os.environ.get("API_CORS_ORIGINS", "").split(",")
            if h.strip()
        ]
    )


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config()
