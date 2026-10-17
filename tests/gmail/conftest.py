"""Builders for Gmail API message payloads (format="full") and an in-memory Gmail client.

Hand-built from a real newsletter (anonymised) until real payloads are captured with
`make auth`; those will be added as JSON regression fixtures in tests/fixtures/gmail/.
"""

import base64
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from email_summarizer.config.settings import GmailSettings
from email_summarizer.gmail.service import NewsletterGmailService

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


# ----------------------------------
# service fixtures: an in-memory Gmail client
# ----------------------------------
class FakeGmailClient:
    """In-memory stand-in for GmailClient that satisfies the service's GmailReader protocol.

    Holds full payloads by id. Message ids for a query come from `ids_for_query` (default:
    every stored id, newest first, like Gmail). Ids in `errors` make get_message raise the
    mapped exception. The queries and get_message calls received are recorded for asserts.
    """

    def __init__(self, ids_for_query: Callable[[str], list[str]] | None = None) -> None:
        self.messages: dict[str, GmailPart] = {}
        self.errors: dict[str, Exception] = {}
        self.ids_for_query = ids_for_query or self._newest_first
        self.queries: list[str] = []
        self.get_calls: list[tuple[str, str]] = []

    def _newest_first(self, _query: str) -> list[str]:
        """Every stored id, newest first, the order messages.list returns."""
        by_date = sorted(self.messages.values(), key=lambda m: int(m["internalDate"]), reverse=True)
        return [message["id"] for message in by_date]

    def add(self, *messages: GmailPart) -> None:
        for message in messages:
            self.messages[message["id"]] = message

    def get_message(
        self, message_id: str, fmt: str = "full", metadata_headers: list[str] | None = None
    ) -> GmailPart | None:
        """Return the payload, None if unknown (a 404); format="metadata" drops the bodies."""
        self.get_calls.append((message_id, fmt))
        if message_id in self.errors:
            raise self.errors[message_id]
        message = self.messages.get(message_id)
        if message is None or fmt != "metadata":
            return message
        wanted = {name.lower() for name in metadata_headers or []}
        headers = [h for h in message["payload"]["headers"] if h["name"].lower() in wanted]
        return {
            **message,
            "payload": {"mimeType": message["payload"]["mimeType"], "headers": headers},
        }

    def get_message_ids(self, query: str) -> Iterator[str]:
        self.queries.append(query)
        return iter(self.ids_for_query(query))

    def get_current_history_id(self) -> str:
        raise NotImplementedError  # extended for sync in step 6c

    def list_added_message_ids(self, start_history_id: str) -> tuple[list[str], str]:
        raise NotImplementedError  # extended for sync in step 6c


SERVICE_SENDERS = ["news@example.com", "digest@example.org"]
FIXED_NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def fixed_now() -> datetime:
    """The clock make_service gives the service."""
    return FIXED_NOW


@pytest.fixture
def fake_client() -> FakeGmailClient:
    return FakeGmailClient()


@pytest.fixture
def make_service(
    fake_client: FakeGmailClient, tmp_path: Path
) -> Callable[..., NewsletterGmailService]:
    """Build a service over fake_client with a fixed clock; override the allow-list if needed."""

    def _make(senders: list[str] | None = None) -> NewsletterGmailService:
        settings = GmailSettings(
            base_dir=tmp_path,
            newsletter_senders=SERVICE_SENDERS if senders is None else senders,
        )
        return NewsletterGmailService(fake_client, settings, now=lambda: FIXED_NOW)

    return _make


@pytest.fixture
def newsletter_service(
    make_service: Callable[..., NewsletterGmailService],
) -> NewsletterGmailService:
    """Named so it doesn't shadow the root `service` fixture used by test_client.py."""
    return make_service()


@pytest.fixture
def make_newsletter(make_part, make_message) -> Callable[..., GmailPart]:
    """Build a small text newsletter with the given id, From header and receive time."""

    def _make(
        message_id: str,
        sender: str = "News Weekly <news@example.com>",
        *,
        subject: str = "Weekly digest",
        received_at: datetime = datetime(2026, 10, 9, tzinfo=UTC),
    ) -> GmailPart:
        return make_message(
            make_part("text/plain", "Secret body text"),
            headers={"From": sender, "Subject": subject},
            message_id=message_id,
            internal_date=str(int(received_at.timestamp() * 1000)),
        )

    return _make
