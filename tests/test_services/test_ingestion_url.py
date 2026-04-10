"""Tests for URL-based ingestion."""

from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from journal.db.repository import SQLiteEntryRepository
from journal.services.chunking import FixedTokenChunker
from journal.services.ingestion import IngestionService
from journal.vectorstore.store import InMemoryVectorStore


@pytest.fixture
def mock_ocr():
    provider = MagicMock()
    provider.extract_text.return_value = "Today I walked through Vienna and met Atlas for coffee."
    return provider


@pytest.fixture
def mock_transcription():
    provider = MagicMock()
    provider.transcribe.return_value = "Voice journal entry about my day at work."
    return provider


@pytest.fixture
def mock_embeddings():
    provider = MagicMock()
    provider.embed_texts.return_value = [[0.1, 0.2, 0.3]]
    provider.embed_query.return_value = [0.1, 0.2, 0.3]
    return provider


@pytest.fixture
def ingestion_service(db_conn, mock_ocr, mock_transcription, mock_embeddings):
    repo = SQLiteEntryRepository(db_conn)
    vector_store = InMemoryVectorStore()
    return IngestionService(
        repository=repo,
        vector_store=vector_store,
        ocr_provider=mock_ocr,
        transcription_provider=mock_transcription,
        embeddings_provider=mock_embeddings,
        chunker=FixedTokenChunker(max_tokens=150, overlap_tokens=40),
    )


@pytest.fixture
def ingestion_service_with_slack(
    db_conn, mock_ocr, mock_transcription, mock_embeddings,
):
    repo = SQLiteEntryRepository(db_conn)
    vector_store = InMemoryVectorStore()
    return IngestionService(
        repository=repo,
        vector_store=vector_store,
        ocr_provider=mock_ocr,
        transcription_provider=mock_transcription,
        embeddings_provider=mock_embeddings,
        chunker=FixedTokenChunker(max_tokens=150, overlap_tokens=40),
        slack_bot_token="xoxb-test-token-123",
    )


def _mock_urlopen(data: bytes, content_type: str = "image/jpeg"):
    """Create a mock urllib response."""
    response = MagicMock()
    response.read.return_value = data
    response.headers = {"Content-Type": content_type}
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)
    return response


class TestIngestImageFromUrl:
    @patch("journal.services.ingestion.urlopen")
    def test_downloads_and_ingests(self, mock_url, ingestion_service, mock_ocr):
        mock_url.return_value = _mock_urlopen(b"fake image bytes")

        entry = ingestion_service.ingest_image_from_url(
            url="https://files.slack.com/image.jpg",
            date="2026-03-22",
        )

        assert entry.entry_date == "2026-03-22"
        assert entry.source_type == "ocr"
        mock_ocr.extract_text.assert_called_once_with(b"fake image bytes", "image/jpeg")

    @patch("journal.services.ingestion.urlopen")
    def test_uses_explicit_media_type(self, mock_url, ingestion_service, mock_ocr):
        mock_url.return_value = _mock_urlopen(b"png data", content_type="application/octet-stream")

        ingestion_service.ingest_image_from_url(
            url="https://example.com/photo.png",
            date="2026-03-22",
            media_type="image/png",
        )

        mock_ocr.extract_text.assert_called_once_with(b"png data", "image/png")

    @patch("journal.services.ingestion.urlopen")
    def test_infers_media_type_from_response(self, mock_url, ingestion_service, mock_ocr):
        mock_url.return_value = _mock_urlopen(b"data", content_type="image/webp")

        ingestion_service.ingest_image_from_url(
            url="https://example.com/photo",
            date="2026-03-22",
        )

        mock_ocr.extract_text.assert_called_once_with(b"data", "image/webp")

    @patch("journal.services.ingestion.urlopen")
    def test_download_failure_raises(self, mock_url, ingestion_service):
        mock_url.side_effect = URLError("Connection refused")

        with pytest.raises(ValueError, match="Failed to download"):
            ingestion_service.ingest_image_from_url(
                url="https://example.com/broken",
                date="2026-03-22",
            )

    @patch("journal.services.ingestion.urlopen")
    def test_http_error_raises(self, mock_url, ingestion_service):
        mock_url.side_effect = HTTPError(
            url="https://example.com/forbidden",
            code=403,
            msg="Forbidden",
            hdrs=MagicMock(),
            fp=None,
        )

        with pytest.raises(ValueError, match="Failed to download.*403"):
            ingestion_service.ingest_image_from_url(
                url="https://example.com/forbidden",
                date="2026-03-22",
            )

    @patch("journal.services.ingestion.urlopen")
    def test_duplicate_detection(self, mock_url, ingestion_service):
        mock_url.return_value = _mock_urlopen(b"same image data")

        ingestion_service.ingest_image_from_url(
            url="https://example.com/page1.jpg",
            date="2026-03-22",
        )

        mock_url.return_value = _mock_urlopen(b"same image data")

        with pytest.raises(ValueError, match="already ingested"):
            ingestion_service.ingest_image_from_url(
                url="https://example.com/page1.jpg",
                date="2026-03-23",
            )


class TestSlackUrlAuth:
    @patch("journal.services.ingestion.urlopen")
    def test_adds_bearer_header_for_slack_urls(
        self, mock_url, ingestion_service_with_slack,
    ):
        mock_url.return_value = _mock_urlopen(b"slack image")

        ingestion_service_with_slack.ingest_image_from_url(
            url="https://files.slack.com/files-pri/T0X-F0X/journal.jpg",
            date="2026-03-22",
        )

        req = mock_url.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer xoxb-test-token-123"

    @patch("journal.services.ingestion.urlopen")
    def test_no_auth_header_for_non_slack_urls(
        self, mock_url, ingestion_service_with_slack,
    ):
        mock_url.return_value = _mock_urlopen(b"other image")

        ingestion_service_with_slack.ingest_image_from_url(
            url="https://example.com/photo.jpg",
            date="2026-03-22",
        )

        req = mock_url.call_args[0][0]
        assert req.get_header("Authorization") is None

    @patch("journal.services.ingestion.urlopen")
    def test_no_auth_header_when_token_not_configured(
        self, mock_url, ingestion_service,
    ):
        mock_url.return_value = _mock_urlopen(b"slack image")

        ingestion_service.ingest_image_from_url(
            url="https://files.slack.com/files-pri/T0X-F0X/journal.jpg",
            date="2026-03-22",
        )

        req = mock_url.call_args[0][0]
        assert req.get_header("Authorization") is None


class TestIngestVoiceFromUrl:
    @patch("journal.services.ingestion.urlopen")
    def test_downloads_and_transcribes(self, mock_url, ingestion_service, mock_transcription):
        mock_url.return_value = _mock_urlopen(b"fake audio bytes", content_type="audio/mp3")

        entry = ingestion_service.ingest_voice_from_url(
            url="https://example.com/note.mp3",
            date="2026-03-22",
        )

        assert entry.entry_date == "2026-03-22"
        assert entry.source_type == "voice"
        mock_transcription.transcribe.assert_called_once_with(
            b"fake audio bytes", "audio/mp3", "en",
        )

    @patch("journal.services.ingestion.urlopen")
    def test_passes_language(self, mock_url, ingestion_service, mock_transcription):
        mock_url.return_value = _mock_urlopen(b"audio", content_type="audio/m4a")

        ingestion_service.ingest_voice_from_url(
            url="https://example.com/note.m4a",
            date="2026-03-22",
            language="nl",
        )

        mock_transcription.transcribe.assert_called_once_with(b"audio", "audio/m4a", "nl")
