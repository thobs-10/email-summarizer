"""Fakes and fixtures for the MCP server tests: no network, no token, no real state file."""

import base64
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from email_summarizer.config.settings import GmailSettings
from email_summarizer.gmail.service import NewsletterGmailService
from email_summarizer.mcp_server.server import build_server
from email_summarizer.models.gmail_views import NewsletterRef
from email_summarizer.models.raw_email import RawEmail
from email_summarizer.state.gmail_state import SyncState

SENDERS = ["news@example.com", "digest@example.org"]
RECEIVED = datetime(2026, 10, 9, 8, 30, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests on asyncio only (FastMCP's own backend)."""
    return "asyncio"


class FakeNewsletterService:
    """In-memory NewsletterReader recording the (already clamped) arguments it receives.

    fetch() mirrors NewsletterGmailService.fetch: None when the id is unknown or the
    sender is not allow-listed. Setting `error` makes find and fetch raise it.
    """

    def __init__(self, senders: list[str]) -> None:
        self.allowed = {sender.lower() for sender in senders}
        self.refs: list[NewsletterRef] = []
        self.emails: dict[str, RawEmail] = {}
        self.error: Exception | None = None
        self.find_calls: list[tuple[str | None, int, int]] = []
        self.fetch_calls: list[str] = []

    def is_allowed(self, sender_email: str) -> bool:
        return sender_email.lower() in self.allowed

    def find(self, sender: str | None, days: int, max_results: int) -> list[NewsletterRef]:
        self.find_calls.append((sender, days, max_results))
        if self.error is not None:
            raise self.error
        return self.refs[:max_results]

    def fetch(self, message_id: str) -> RawEmail | None:
        self.fetch_calls.append(message_id)
        if self.error is not None:
            raise self.error
        email = self.emails.get(message_id)
        return email if email is not None and self.is_allowed(email.sender_email) else None


class FakeStateStore:
    """In-memory SyncStateReader; setting `error` makes load raise it."""

    def __init__(self) -> None:
        self.state = SyncState()
        self.error: Exception | None = None
        self.loads = 0

    def load(self) -> SyncState:
        self.loads += 1
        if self.error is not None:
            raise self.error
        return self.state


@pytest.fixture
def mcp_settings(tmp_path: Path) -> GmailSettings:
    """Real settings rooted in tmp_path; days_back=7 caps find_newsletters at 28 days."""
    return GmailSettings(base_dir=tmp_path, newsletter_senders=SENDERS, days_back=7)


@pytest.fixture
def fake_service() -> FakeNewsletterService:
    return FakeNewsletterService(SENDERS)


@pytest.fixture
def fake_store() -> FakeStateStore:
    return FakeStateStore()


@pytest.fixture
def server(
    fake_service: FakeNewsletterService, fake_store: FakeStateStore, mcp_settings: GmailSettings
) -> FastMCP:
    return build_server(fake_service, fake_store, mcp_settings)


@pytest.fixture
async def client(server: FastMCP) -> AsyncIterator[Client]:
    """An in-memory MCP client connected to `server`."""
    async with Client(server) as connected:
        yield connected


@pytest.fixture
def make_ref() -> Callable[..., NewsletterRef]:
    def _make(message_id: str = "m1", sender_email: str = "news@example.com") -> NewsletterRef:
        return NewsletterRef(
            message_id=message_id,
            sender_email=sender_email,
            subject="Weekly digest",
            received_at=RECEIVED,
        )

    return _make


# ----------------------------------
# the real service over a stub Gmail client
# ----------------------------------
def gmail_message(message_id: str, from_header: str, body: str) -> dict[str, Any]:
    """A minimal text/plain Gmail message resource, as messages.get(format="full") returns."""
    return {
        "id": message_id,
        "threadId": f"t{message_id}",
        "labelIds": ["INBOX"],
        "internalDate": str(int(RECEIVED.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": from_header},
                {"name": "Subject", "value": "Weekly digest"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


class StubGmailReader:
    """GmailReader stub serving whole messages for any format.

    The history methods fail the test: MCP tools must never run the sync path.
    """

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = {message["id"]: message for message in messages}

    def get_message(
        self, message_id: str, fmt: str = "full", metadata_headers: list[str] | None = None
    ) -> dict[str, Any] | None:
        return self.messages.get(message_id)

    def get_message_ids(self, query: str) -> Iterator[str]:
        return iter(self.messages)

    def get_current_history_id(self) -> str:
        raise AssertionError("MCP tools must never sync")

    def list_added_message_ids(self, start_history_id: str) -> tuple[list[str], str]:
        raise AssertionError("MCP tools must never sync")


@pytest.fixture
def real_service_server(mcp_settings: GmailSettings, fake_store: FakeStateStore) -> FastMCP:
    """build_server over the real NewsletterGmailService: one allow-listed, one spoofed mail."""
    reader = StubGmailReader(
        [
            gmail_message("good1", "News <news@example.com>", "Allowed body"),
            gmail_message("evil1", '"news@example.com" <alerts@bank.example>', "Bank secret"),
        ]
    )
    service = NewsletterGmailService(reader, mcp_settings, now=lambda: RECEIVED)
    return build_server(service, fake_store, mcp_settings)
