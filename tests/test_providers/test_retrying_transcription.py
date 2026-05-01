"""Tests for RetryingTranscriptionProvider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from google.genai import errors as genai_errors

from journal.models import TranscriptionResult
from journal.providers.transcription import (
    PrimaryExhaustedError,
    RetryingTranscriptionProvider,
    TranscriptionProvider,
)


def _make_result(text: str = "ok") -> TranscriptionResult:
    return TranscriptionResult(text=text, uncertain_spans=[])


def _openai_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")


def _api_timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_openai_request())


def _auth_error() -> openai.AuthenticationError:
    req = _openai_request()
    return openai.AuthenticationError(
        message="bad key",
        response=httpx.Response(401, request=req),
        body=None,
    )


def _bad_request_error() -> openai.BadRequestError:
    req = _openai_request()
    return openai.BadRequestError(
        message="bad input",
        response=httpx.Response(400, request=req),
        body=None,
    )


def _client_error(code: int) -> genai_errors.ClientError:
    return genai_errors.ClientError(
        code=code,
        response_json={"error": {"message": f"code-{code}"}},
    )


def _server_error(code: int = 503) -> genai_errors.ServerError:
    return genai_errors.ServerError(
        code=code,
        response_json={"error": {"message": "down"}},
    )


class TestRetryingTranscriptionProvider:
    def test_implements_protocol(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        wrapper = RetryingTranscriptionProvider(primary=primary)
        assert isinstance(wrapper, TranscriptionProvider)

    def test_primary_success_first_attempt(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        fallback = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.return_value = _make_result("done")

        wrapper = RetryingTranscriptionProvider(primary=primary, fallback=fallback)
        with patch("journal.providers.transcription.time.sleep") as mock_sleep:
            result = wrapper.transcribe(b"audio", "audio/mpeg")

        assert result.text == "done"
        primary.transcribe.assert_called_once_with(b"audio", "audio/mpeg", "en")
        fallback.transcribe.assert_not_called()
        mock_sleep.assert_not_called()

    def test_primary_success_after_one_retry(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = [_api_timeout(), _make_result("ok")]

        wrapper = RetryingTranscriptionProvider(primary=primary)
        with patch("journal.providers.transcription.time.sleep") as mock_sleep:
            result = wrapper.transcribe(b"audio", "audio/mpeg")

        assert result.text == "ok"
        assert primary.transcribe.call_count == 2
        assert mock_sleep.call_count == 1

    def test_primary_exhaustion_falls_back_to_secondary(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = _api_timeout()
        fallback = MagicMock(spec=TranscriptionProvider)
        fallback.transcribe.return_value = _make_result("from-fallback")

        wrapper = RetryingTranscriptionProvider(
            primary=primary, fallback=fallback, max_attempts=3,
        )
        with patch("journal.providers.transcription.time.sleep"):
            result = wrapper.transcribe(b"audio", "audio/wav", language="en")

        assert result.text == "from-fallback"
        assert primary.transcribe.call_count == 3
        fallback.transcribe.assert_called_once_with(b"audio", "audio/wav", "en")

    def test_primary_exhaustion_no_fallback_raises_PrimaryExhaustedError(self) -> None:  # noqa: N802
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = _api_timeout()

        wrapper = RetryingTranscriptionProvider(primary=primary, max_attempts=3)
        with (
            patch("journal.providers.transcription.time.sleep"),
            pytest.raises(PrimaryExhaustedError) as excinfo,
        ):
            wrapper.transcribe(b"audio", "audio/mpeg")

        assert excinfo.value.attempts == 3
        assert isinstance(excinfo.value.last_error, openai.APITimeoutError)
        assert "3 attempt" in str(excinfo.value)
        assert primary.transcribe.call_count == 3

    def test_non_transient_bubbles_immediately(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        fallback = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = _auth_error()

        wrapper = RetryingTranscriptionProvider(primary=primary, fallback=fallback)
        with (
            patch("journal.providers.transcription.time.sleep") as mock_sleep,
            pytest.raises(openai.AuthenticationError),
        ):
            wrapper.transcribe(b"audio", "audio/mpeg")

        primary.transcribe.assert_called_once()
        fallback.transcribe.assert_not_called()
        mock_sleep.assert_not_called()

    def test_non_transient_after_one_retry_bubbles(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        fallback = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = [_api_timeout(), _bad_request_error()]

        wrapper = RetryingTranscriptionProvider(primary=primary, fallback=fallback)
        with (
            patch("journal.providers.transcription.time.sleep"),
            pytest.raises(openai.BadRequestError),
        ):
            wrapper.transcribe(b"audio", "audio/mpeg")

        assert primary.transcribe.call_count == 2
        fallback.transcribe.assert_not_called()

    def test_gemini_429_is_transient(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = [_client_error(429), _make_result("ok")]

        wrapper = RetryingTranscriptionProvider(primary=primary)
        with patch("journal.providers.transcription.time.sleep"):
            result = wrapper.transcribe(b"audio", "audio/mpeg")

        assert result.text == "ok"
        assert primary.transcribe.call_count == 2

    def test_gemini_403_bubbles(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = _client_error(403)

        wrapper = RetryingTranscriptionProvider(primary=primary)
        with (
            patch("journal.providers.transcription.time.sleep") as mock_sleep,
            pytest.raises(genai_errors.ClientError),
        ):
            wrapper.transcribe(b"audio", "audio/mpeg")

        primary.transcribe.assert_called_once()
        mock_sleep.assert_not_called()

    def test_gemini_server_error_is_transient(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = [_server_error(503), _make_result("ok")]

        wrapper = RetryingTranscriptionProvider(primary=primary)
        with patch("journal.providers.transcription.time.sleep"):
            result = wrapper.transcribe(b"audio", "audio/mpeg")

        assert result.text == "ok"
        assert primary.transcribe.call_count == 2

    def test_httpx_timeout_is_transient(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = [
            httpx.TimeoutException("timed out"),
            _make_result("ok"),
        ]

        wrapper = RetryingTranscriptionProvider(primary=primary)
        with patch("journal.providers.transcription.time.sleep"):
            result = wrapper.transcribe(b"audio", "audio/mpeg")

        assert result.text == "ok"
        assert primary.transcribe.call_count == 2

    def test_exponential_backoff_delays(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        # Always raise transient → exhaust 5 attempts → 4 sleeps.
        primary.transcribe.side_effect = _api_timeout()

        wrapper = RetryingTranscriptionProvider(
            primary=primary,
            max_attempts=5,
            base_delay=1.0,
            max_delay=30.0,
        )
        with (
            patch("journal.providers.transcription.time.sleep") as mock_sleep,
            pytest.raises(PrimaryExhaustedError),
        ):
            wrapper.transcribe(b"audio", "audio/mpeg")

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        # base * 2**attempt for attempt in 0..3 → 1, 2, 4, 8
        assert delays == [1.0, 2.0, 4.0, 8.0]

    def test_max_delay_caps_long_sequences(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = _api_timeout()

        wrapper = RetryingTranscriptionProvider(
            primary=primary,
            max_attempts=5,
            base_delay=1.0,
            max_delay=2.0,
        )
        with (
            patch("journal.providers.transcription.time.sleep") as mock_sleep,
            pytest.raises(PrimaryExhaustedError),
        ):
            wrapper.transcribe(b"audio", "audio/mpeg")

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 2.0, 2.0]

    def test_fallback_exception_bubbles(self) -> None:
        primary = MagicMock(spec=TranscriptionProvider)
        primary.transcribe.side_effect = _api_timeout()
        fallback = MagicMock(spec=TranscriptionProvider)
        fallback.transcribe.side_effect = RuntimeError("fallback exploded")

        wrapper = RetryingTranscriptionProvider(
            primary=primary, fallback=fallback, max_attempts=2,
        )
        with (
            patch("journal.providers.transcription.time.sleep"),
            pytest.raises(RuntimeError, match="fallback exploded"),
        ):
            wrapper.transcribe(b"audio", "audio/mpeg")

        assert primary.transcribe.call_count == 2
        fallback.transcribe.assert_called_once()
