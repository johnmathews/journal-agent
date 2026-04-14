"""Tests for EmailService."""

from unittest.mock import MagicMock, patch

import pytest

from journal.services.email import EmailService


@pytest.fixture
def email_svc() -> EmailService:
    return EmailService(
        smtp_host="smtp.test.local",
        smtp_port=465,
        smtp_username="user@test.local",
        smtp_password="test-password",
        from_email="noreply@test.local",
    )


class TestSendSync:
    @patch("journal.services.email.smtplib.SMTP_SSL")
    def test_send_sync_sends_message(
        self, mock_smtp_class: MagicMock, email_svc: EmailService
    ) -> None:
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        email_svc._send_sync("to@example.com", "Test Subject", "<p>Hello</p>")

        mock_smtp_class.assert_called_once_with("smtp.test.local", 465)
        mock_server.login.assert_called_once_with("user@test.local", "test-password")
        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]
        assert msg["From"] == "noreply@test.local"
        assert msg["To"] == "to@example.com"
        assert msg["Subject"] == "Test Subject"


class TestSendAsync:
    @pytest.mark.asyncio
    @patch("journal.services.email.smtplib.SMTP_SSL")
    async def test_send_calls_smtp(
        self, mock_smtp_class: MagicMock, email_svc: EmailService
    ) -> None:
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        await email_svc.send("async@example.com", "Async Test", "<p>Async</p>")

        mock_server.login.assert_called_once()
        mock_server.send_message.assert_called_once()


class TestVerificationEmail:
    @pytest.mark.asyncio
    @patch("journal.services.email.smtplib.SMTP_SSL")
    async def test_verification_email_contains_link(
        self, mock_smtp_class: MagicMock, email_svc: EmailService
    ) -> None:
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        await email_svc.send_verification_email(
            "user@example.com", "tok-abc", "https://app.example.com"
        )

        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]
        assert msg["Subject"] == "Verify your email - Journal Insights"
        # Check the HTML payload contains the link
        html_part = msg.get_payload()[0].get_payload()
        assert "https://app.example.com/verify-email?token=tok-abc" in html_part
        assert "24 hours" in html_part


class TestPasswordResetEmail:
    @pytest.mark.asyncio
    @patch("journal.services.email.smtplib.SMTP_SSL")
    async def test_reset_email_contains_link(
        self, mock_smtp_class: MagicMock, email_svc: EmailService
    ) -> None:
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        await email_svc.send_password_reset_email(
            "user@example.com", "tok-reset", "https://app.example.com"
        )

        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]
        assert msg["Subject"] == "Reset your password - Journal Insights"
        html_part = msg.get_payload()[0].get_payload()
        assert "https://app.example.com/reset-password?token=tok-reset" in html_part
        assert "30 minutes" in html_part
