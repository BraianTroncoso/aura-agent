"""gmail_imap.py — read Gmail over IMAP with an App Password (no OAuth).

The lightest honest path for a personal assistant: the user creates a Gmail
App Password once; Aura reads recent messages via imap.gmail.com. Read-only —
Aura never sends or deletes here.
"""

from __future__ import annotations

import email
import imaplib
import logging
import smtplib
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parseaddr

from app.config import settings

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(to: str, subject: str, body: str) -> None:
    """Send an email from the user's Gmail via SMTP. Raises on failure."""
    password = (settings.gmail_app_password or "").replace(" ", "")
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = settings.gmail_address
    msg["To"] = to
    msg["Subject"] = subject
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(settings.gmail_address, password)
        s.sendmail(settings.gmail_address, [to], msg.as_string())


def is_configured() -> bool:
    return bool(settings.gmail_address and settings.gmail_app_password)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out).strip()


def _snippet(msg: email.message.Message, limit: int = 200) -> str:
    """Best-effort plain-text preview of a message body."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and "attachment" not in str(
                    part.get("Content-Disposition", "")
                ):
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()[:limit]
            return ""
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace").strip()[:limit]
    except Exception:
        return ""


def fetch_recent(n: int = 5) -> list[dict]:
    """Return the latest ``n`` inbox messages: from, subject, date, snippet.

    Raises on auth/connection errors so the caller can report honestly.
    """
    password = (settings.gmail_app_password or "").replace(" ", "")
    m = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        m.login(settings.gmail_address, password)
        m.select("INBOX")
        typ, data = m.search(None, "ALL")
        ids = data[0].split()
        latest = ids[-n:] if ids else []
        out: list[dict] = []
        for mid in reversed(latest):
            typ, msg_data = m.fetch(mid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            name, addr = parseaddr(msg.get("From", ""))
            body = _snippet(msg, limit=2500)
            out.append({
                "from": _decode(name) or addr,
                "from_email": addr,
                "subject": _decode(msg.get("Subject")),
                "date": msg.get("Date", ""),
                "snippet": body[:160],
                "body": body,
            })
        return out
    finally:
        try:
            m.logout()
        except Exception:
            pass
