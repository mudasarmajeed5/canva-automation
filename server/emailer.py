"""Job completion email reports via Google SMTP (app password)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from config import get_settings

log = logging.getLogger(__name__)


def send_upload_report(to: str, subject: str, lines: list[str]) -> bool:
    """Send a plain-text upload report. Never raises — returns False on failure."""
    settings = get_settings()
    recipient = (to or "").strip() or settings.email_fallback_to
    if not recipient:
        log.warning("no email recipient configured; skipping upload report")
        return False
    if not settings.smtp_app_password:
        log.warning("SMTP_APP_PASSWORD not set; skipping upload report")
        return False

    body = "\n".join(f"• {line}" for line in lines) or "No files were processed."
    message = EmailMessage()
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_app_password)
            server.send_message(message)
        log.info("upload report emailed to %s (%d line(s))", recipient, len(lines))
        return True
    except Exception:
        log.exception("failed to send upload report to %s", recipient)
        return False
