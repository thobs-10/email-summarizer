"""Pytest configuration and fixtures for the test suite."""

from collections.abc import Iterator
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# from pytz import UTC
import pytest
from google.oauth2.credentials import Credentials
from loguru import logger

from email_summarizer.config.settings import GmailSettings
from email_summarizer.models.raw_email import RawEmail


# ----------------------------------
# gmail auth fixtures
# ----------------------------------
@pytest.fixture
def gmail_settings(tmp_path) -> GmailSettings:
    """Real GmailSettings rooted in tmp_path: missing fields fail tests, real files are never touched."""
    return GmailSettings(base_dir=tmp_path)


@pytest.fixture
def log_messages() -> Iterator[list[str]]:
    """Collect loguru messages (loguru bypasses caplog, and its stderr sink bypasses capsys)."""
    messages: list[str] = []
    handler_id = logger.add(lambda message: messages.append(str(message)), format="{message}")
    yield messages
    logger.remove(handler_id)


@pytest.fixture
def make_creds():
    """Factory for fake Credentials objects with controllable state."""

    def _make(valid=True, expired=False, refresh_token="refresh-token"):
        creds = MagicMock(spec=Credentials)
        creds.valid = valid
        creds.expired = expired
        creds.refresh_token = refresh_token
        creds.has_scopes.return_value = True
        creds.to_json.return_value = '{"token": "fake"}'
        return creds

    return _make


# ----------------------------------
# gmail client fixtures and helpers
# ----------------------------------
@pytest.fixture
def service():
    """Stand-in for the object returned by googleapiclient's build()."""
    return MagicMock(name="gmail_service")


@pytest.fixture
def client(service):
    from email_summarizer.gmail.client import GmailClient

    MODULE = "email_summarizer.gmail.client"
    with patch(f"{MODULE}.build", return_value=service):
        return GmailClient(credentials=MagicMock(name="creds"), max_retries=3)


# @pytest.fixture
def users_api(service):
    return service.users.return_value


# @pytest.fixture
def messages_api(service):
    return service.users.return_value.messages.return_value


def history_api(service):
    return service.users.return_value.history.return_value


def history_page(records, history_id, next_token=None):
    """Build a history.list response shaped like the real API (both fields present)."""
    page = {
        "history": [
            {
                "id": str(i),
                "messages": [{"id": m, "threadId": m} for m in ids],
                "messagesAdded": [{"message": {"id": m, "threadId": m}} for m in ids],
            }
            for i, ids in enumerate(records, start=1)
        ],
        "historyId": history_id,
    }
    if next_token:
        page["nextPageToken"] = next_token
    return page


# ----------------------------------
# raw email fixtures and helpers
# ----------------------------------
@pytest.fixture
def make_email():
    """Factory for valid RawEmail objects; override any field to test a specific case."""

    def _make(**overrides) -> RawEmail:
        data = {
            "message_id": "msg-1",
            "thread_id": "thread-1",
            "label_ids": ["INBOX", "CATEGORY_UPDATES"],
            "sender_id": "sender-1",
            "sender_email": "news@example.com",
            "received_at": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            "subject": "Weekly digest",
            "html_body": "<p>Hello</p>",
            "text_body": "Hello",
        }
        data.update(overrides)
        return RawEmail(**data)

    return _make
