"""Builders for Gmail API message payloads (format="full") used by the parser tests.

Hand-built from a real newsletter (anonymised) until real payloads are captured with
`make auth`; those will be added as JSON regression fixtures in tests/fixtures/gmail/.
"""

import base64
from collections.abc import Callable
from typing import Any

import pytest

GmailPart = dict[str, Any]

LINKEDIN_FROM = '"Towards AI, Inc. via LinkedIn" <newsletters-noreply@linkedin.com>'
LINKEDIN_SUBJECT = "TAI #224: Personal Agents Get Their Own Computers"
LINKEDIN_TEXT = (
    "TAI #224: Personal Agents Get Their Own Computers\n"
    "What happened this week in AI by Louie\n"
    "The past week brought another rush of model releases.\n"
    "...\nKeep reading on LinkedIn\n"
    "This email was intended for Alex Reader.\n"
    "Unsubscribe · Help\n"
)
LINKEDIN_HTML = (
    "<html><body>"
    '<img alt="Messaging icon"><h1>TAI #224: Personal Agents Get Their Own Computers</h1>'
    "<h2>What happened this week in AI by Louie</h2>"
    "<p>The past week brought another rush of model releases.</p>"
    '<a href="https://www.linkedin.com/pulse/example">Keep reading on LinkedIn</a>'
    "<p>This email was intended for Alex Reader.</p>"
    "</body></html>"
)
# 2026-09-29T16:27:00.123Z in epoch milliseconds, as Gmail returns it (a string).
INTERNAL_DATE = "1790699220123"


def encode(text: str, charset: str = "utf-8", pad: bool = True) -> str:
    """Base64url-encode text the way Gmail does; pad=False mimics unpadded data."""
    data = base64.urlsafe_b64encode(text.encode(charset)).decode("ascii")
    return data if pad else data.rstrip("=")


@pytest.fixture
def make_part() -> Callable[..., GmailPart]:
    """Build one MIME part: a text body, an attachment, or a multipart container."""

    def _make(
        mime_type: str,
        body: str | None = None,
        *,
        charset: str = "utf-8",
        filename: str = "",
        parts: list[GmailPart] | None = None,
        attachment_id: str | None = None,
        data: str | None = None,
        pad: bool = True,
    ) -> GmailPart:
        part: GmailPart = {"mimeType": mime_type, "filename": filename, "headers": []}
        if parts is not None:
            part["body"] = {"size": 0}
            part["parts"] = parts
        elif attachment_id:
            part["body"] = {"attachmentId": attachment_id, "size": 1024}
        else:
            part["headers"] = [
                {"name": "Content-Type", "value": f'{mime_type}; charset="{charset}"'}
            ]
            encoded = data if data is not None else encode(body or "", charset, pad)
            part["body"] = {"size": len(body or ""), "data": encoded}
        return part

    return _make


@pytest.fixture
def make_message() -> Callable[..., GmailPart]:
    """Wrap a payload part in a Gmail message with top-level headers (From, Subject, ...)."""

    def _make(
        payload: GmailPart,
        *,
        headers: dict[str, str] | None = None,
        message_id: str = "msg-1",
        thread_id: str = "thread-1",
        label_ids: list[str] | None = None,
        internal_date: str = INTERNAL_DATE,
    ) -> GmailPart:
        top = {"From": LINKEDIN_FROM, "Subject": LINKEDIN_SUBJECT} if headers is None else headers
        payload = {
            **payload,
            "headers": payload["headers"]
            + [{"name": name, "value": value} for name, value in top.items()],
        }
        return {
            "id": message_id,
            "threadId": thread_id,
            "labelIds": ["INBOX", "CATEGORY_UPDATES"] if label_ids is None else label_ids,
            "internalDate": internal_date,
            "payload": payload,
        }

    return _make


@pytest.fixture
def linkedin_message(make_part, make_message) -> GmailPart:
    """The common newsletter shape: multipart/alternative with text and HTML."""
    return make_message(
        make_part(
            "multipart/alternative",
            parts=[make_part("text/plain", LINKEDIN_TEXT), make_part("text/html", LINKEDIN_HTML)],
        )
    )


@pytest.fixture
def linkedin_bodies() -> dict[str, str]:
    """Expected decoded values for linkedin_message."""
    return {"text": LINKEDIN_TEXT, "html": LINKEDIN_HTML, "subject": LINKEDIN_SUBJECT}
