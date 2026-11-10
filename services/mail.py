"""Outgoing email.

Backends (config MAIL_BACKEND)
- "console": print the message (development), so you can click verification links.
- "smtp": send through any SMTP provider (Brevo, Resend, Mailgun, SES, Gmail...).

Two entry points
- send_email(): raises EmailError on failure. Use it when the user must know the email
  went out (verification links); app.py turns EmailError into a friendly message.
- notify(): best effort. Logs and returns False on failure. Use it for courtesy
  notifications sent AFTER something was already saved (approvals, removals), so a mail
  outage can't make a finished action look like it failed.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.parse import urlparse

from flask import current_app

log = logging.getLogger(__name__)


class EmailError(RuntimeError):
    """The message could not be sent."""


def _build_message(to: str, subject: str, body: str) -> EmailMessage:
    cfg = current_app.config
    message = EmailMessage()
    message["From"] = cfg["MAIL_FROM"] or "Campus Market <no-reply@localhost>"
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=urlparse(cfg["PUBLIC_BASE_URL"]).hostname or None)
    message.set_content(body)
    return message


def _send_smtp(message: EmailMessage) -> None:
    cfg = current_app.config
    context = ssl.create_default_context()
    try:
        if cfg["SMTP_USE_SSL"]:
            server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT"], context=context)
        else:
            server = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT"])
        with server:
            if cfg["SMTP_USE_TLS"] and not cfg["SMTP_USE_SSL"]:
                server.starttls(context=context)
            if cfg["SMTP_USERNAME"]:
                server.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"])
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"SMTP send failed: {exc}") from exc


def send_email(to: str, subject: str, body: str) -> None:
    try:
        message = _build_message(to, subject, body)
    except ValueError as exc:  # e.g. a newline smuggled into a header
        raise EmailError(f"Invalid email headers: {exc}") from exc

    if current_app.testing:  # tests read messages from here; never grows in production
        current_app.extensions.setdefault("outbox", []).append({"to": to, "subject": subject, "body": body})

    backend = current_app.config["MAIL_BACKEND"]
    if backend == "smtp":
        _send_smtp(message)
    elif backend == "console":
        if not current_app.testing:
            print(f"\n--- EMAIL to {to} ---\nSubject: {subject}\n\n{body}\n--- END EMAIL ---\n", flush=True)
    else:
        raise EmailError(f"Unknown MAIL_BACKEND {backend!r}")


def notify(to: str, subject: str, body: str) -> bool:
    try:
        send_email(to, subject, body)
        return True
    except EmailError as exc:
        log.warning("Could not send %r to %s: %s", subject, to, exc)
        return False
