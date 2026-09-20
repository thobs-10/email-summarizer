"""Pytest configuration and fixtures for the test suite."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# from pytz import UTC
import pytest
from google.oauth2.credentials import Credentials

from email_summarizer.models.raw_email import RawEmail


@pytest.fixture
def get_app_settings(tmp_path):
    """Minimal stand-in for AppSettings.

    get_credentials() only reads `client_secret_file`, so a SimpleNamespace is enough and
    avoids loading a real .env or requiring every other setting to be present.
    """
    secret_file = tmp_path / "client_secret.json"
    secret_file.write_text("{}")  # never parsed: the OAuth flow is mocked in the tests
    return SimpleNamespace(client_secret_file=str(secret_file))


@pytest.fixture
def token_path(tmp_path, monkeypatch):
    """Point TOKEN_PATH at a temp file so tests never touch a real token.json."""
    path = tmp_path / "token.json"
    monkeypatch.setenv("TOKEN_PATH", str(path))
    return path


@pytest.fixture
def make_creds():
    """Factory for fake Credentials objects with controllable state."""

    def _make(valid=True, expired=False, refresh_token="refresh-token"):
        creds = MagicMock(spec=Credentials)
        creds.valid = valid
        creds.expired = expired
        creds.refresh_token = refresh_token
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
