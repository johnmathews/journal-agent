"""Email service for sending verification and password-reset emails.

Uses ``smtplib`` with ``asyncio.to_thread`` for non-blocking sends.
The volume is tiny (registration + password reset only), so a full
async SMTP library is unnecessary.
"""

import asyncio
import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


class EmailService:
    """SMTP email sender for transactional emails (verification, password reset)."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        from_email: str,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._username = smtp_username
        self._password = smtp_password
        self._from = from_email

    def _send_sync(self, to: str, subject: str, html: str) -> None:
        """Send an email synchronously via SMTP_SSL."""
        msg = MIMEMultipart("alternative")
        msg["From"] = self._from
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL(self._host, self._port) as server:
            server.login(self._username, self._password)
            server.send_message(msg)
        log.info("Email sent to %s: %s", to, subject)

    async def send(self, to: str, subject: str, html: str) -> None:
        """Send an email asynchronously (runs SMTP in a thread)."""
        await asyncio.to_thread(self._send_sync, to, subject, html)

    async def send_verification_email(
        self, to: str, token: str, base_url: str
    ) -> None:
        """Send an email-verification link to the given address."""
        safe_base = html.escape(base_url, quote=True)
        safe_token = html.escape(token, quote=True)
        link = f"{safe_base}/verify-email?token={safe_token}"
        body = (
            "<h2>Verify your email</h2>"
            "<p>Click the link below to verify your email address:</p>"
            f'<p><a href="{link}">Verify Email</a></p>'
            "<p>This link expires in 24 hours.</p>"
            "<p>If you didn't create an account, you can ignore this email.</p>"
        )
        await self.send(to, "Verify your email - Journal Insights", body)

    async def send_password_reset_email(
        self, to: str, token: str, base_url: str
    ) -> None:
        """Send a password-reset link to the given address."""
        safe_base = html.escape(base_url, quote=True)
        safe_token = html.escape(token, quote=True)
        link = f"{safe_base}/reset-password?token={safe_token}"
        body = (
            "<h2>Reset your password</h2>"
            "<p>Click the link below to reset your password:</p>"
            f'<p><a href="{link}">Reset Password</a></p>'
            "<p>This link expires in 30 minutes.</p>"
            "<p>If you didn't request this, you can ignore this email.</p>"
        )
        await self.send(to, "Reset your password - Journal Insights", body)
