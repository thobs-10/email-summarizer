"""Tests for the sync CLI run by `make sync` (Gmail is faked; no network, no real token)."""

from datetime import UTC, datetime
from http.client import IncompleteRead
from typing import Any
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from pydantic import ValidationError

from email_summarizer.cli.sync import StateFileError, _build, main, run_sync
from email_summarizer.config.settings import AppSettings, GmailSettings
from email_summarizer.gmail.service import EmptyAllowListError, NewsletterGmailService
from email_summarizer.ingestion.sinks import JsonFileSink
from email_summarizer.models.gmail_views import SyncResult
from email_summarizer.state.gmail_state import JsonStateStore, SyncState

MODULE = "email_summarizer.cli.sync"
OLD_STATE = SyncState(history_id="100", last_synced=datetime(2026, 10, 1, tzinfo=UTC))
NEW_STATE = SyncState(history_id="900", last_synced=datetime(2026, 10, 10, tzinfo=UTC))
# Stand-ins for email content that must never reach the logs.
SECRET_SUBJECT = "TAI #224: Personal Agents Get Their Own Computers"
SECRET_BODY = "This email was intended for Alex Reader."


def make_result(**overrides) -> SyncResult:
    data: dict[str, Any] = {
        "mode": "history",
        "history_fallback": False,
        "candidates": 3,
        "written": 2,
        "skipped_not_allowed": 1,
        "skipped_missing": 0,
        "parse_errors": 0,
        "new_state": NEW_STATE,
    }
    data.update(overrides)
    return SyncResult(**data)


@pytest.fixture
def app_settings(gmail_settings):
    """Patch get_settings so the CLI never reads the developer's .env."""
    settings = AppSettings(gmail=gmail_settings, _env_file=None)
    with patch(f"{MODULE}.get_settings", return_value=settings):
        yield settings


@pytest.fixture
def store(gmail_settings) -> JsonStateStore:
    """Real state store in tmp_path, pre-seeded with OLD_STATE."""
    store = JsonStateStore(gmail_settings.state_path)
    store.save(OLD_STATE)
    return store


@pytest.fixture
def sink(gmail_settings) -> JsonFileSink:
    return JsonFileSink(gmail_settings.raw_output_dir)


@pytest.fixture
def fake_service() -> MagicMock:
    service = MagicMock(spec=NewsletterGmailService)
    service.sync.return_value = make_result()
    return service


@pytest.fixture
def built(app_settings, fake_service, sink, store):
    """Patch _build so main runs against the fake service and real tmp_path sink/store."""
    with patch(f"{MODULE}._build", return_value=(fake_service, sink, store)) as build:
        yield build


# ----------------------------------
# run_sync
# ----------------------------------
def test_run_sync_passes_loaded_state_and_saves_new_state(fake_service, sink, store):
    result = run_sync(fake_service, sink, store)

    fake_service.sync.assert_called_once_with(OLD_STATE, sink)
    assert result == fake_service.sync.return_value
    assert store.load() == NEW_STATE


def test_run_sync_first_run_uses_empty_state(fake_service, sink, gmail_settings):
    store = JsonStateStore(gmail_settings.state_path)  # no file yet

    run_sync(fake_service, sink, store)

    fake_service.sync.assert_called_once_with(SyncState(), sink)
    assert store.load() == NEW_STATE


@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("allow-list is empty")])
def test_run_sync_failure_propagates_and_leaves_state_file_unchanged(
    fake_service, sink, store, error
):
    before = store.path.read_bytes()
    fake_service.sync.side_effect = error

    with pytest.raises(type(error)):
        run_sync(fake_service, sink, store)

    assert store.path.read_bytes() == before


def test_run_sync_corrupt_state_raises_state_file_error_without_syncing(fake_service, sink, store):
    store.path.write_text('{"history_id": "' + SECRET_SUBJECT + '", "extra": 1}')

    with pytest.raises(StateFileError) as exc:
        run_sync(fake_service, sink, store)

    assert str(store.path) in str(exc.value)
    assert SECRET_SUBJECT not in str(exc.value)
    fake_service.sync.assert_not_called()


# ----------------------------------
# _build
# ----------------------------------
def test_build_wires_components_from_settings(gmail_settings, make_creds):
    creds = make_creds()
    with (
        patch(f"{MODULE}.load_credentials", return_value=creds) as load,
        patch(f"{MODULE}.GmailClient") as client_cls,
        patch(f"{MODULE}.NewsletterGmailService") as service_cls,
    ):
        service, sink, store = _build(gmail_settings)

    load.assert_called_once_with(gmail_settings)
    client_cls.assert_called_once_with(creds, gmail_settings.max_retries)
    service_cls.assert_called_once_with(client_cls.return_value, gmail_settings)
    assert service is service_cls.return_value
    assert sink.directory == gmail_settings.raw_output_dir
    assert store.path == gmail_settings.state_path


# ----------------------------------
# main
# ----------------------------------
def test_success_returns_zero_and_logs_summary(built, store, log_messages):
    assert main([]) == 0

    summary = log_messages[-1]
    for fragment in ("mode=history", "written=2", "candidates=3", "history_fallback=False"):
        assert fragment in summary
    assert str(store.path) in summary
    assert store.load() == NEW_STATE


@pytest.mark.parametrize("token", [None, "not json"], ids=["missing", "invalid"])
def test_unusable_token_returns_one_and_says_make_auth(app_settings, log_messages, token):
    # Real load_credentials: it fails on the token file before any network call.
    if token is not None:
        app_settings.gmail.token_path.parent.mkdir(parents=True, exist_ok=True)
        app_settings.gmail.token_path.write_text(token)

    with patch(f"{MODULE}.GmailClient") as client_cls:
        assert main([]) == 1

    assert "make auth" in log_messages[-1]
    client_cls.assert_not_called()
    assert not app_settings.gmail.state_path.exists()


def test_corrupt_state_file_returns_one_with_path_and_no_contents(
    built, store, fake_service, log_messages
):
    store.path.write_text('{"history_id": "' + SECRET_SUBJECT + '"')  # truncated JSON

    assert main([]) == 1

    assert str(store.path) in log_messages[-1]
    assert "Delete it" in log_messages[-1]
    assert all(SECRET_SUBJECT not in message for message in log_messages)
    fake_service.sync.assert_not_called()


def test_empty_allow_list_returns_one_with_message(built, store, fake_service, log_messages):
    before = store.path.read_bytes()
    fake_service.sync.side_effect = EmptyAllowListError("newsletter allow-list is empty")

    assert main([]) == 1

    assert "newsletter allow-list is empty" in log_messages[-1]
    assert store.path.read_bytes() == before


def _http_error() -> HttpError:
    return HttpError(httplib2.Response({"status": 503}), SECRET_BODY.encode())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(), "HttpError (HTTP 503)"),
        (OSError(f"cannot write data/raw/{SECRET_SUBJECT}.json"), "OSError"),
        (TransportError(SECRET_BODY), "TransportError"),
        (PermissionError(SECRET_SUBJECT), "PermissionError"),
        (httplib2.ServerNotFoundError(SECRET_BODY), "ServerNotFoundError"),
        (IncompleteRead(SECRET_BODY.encode()), "IncompleteRead"),
        (ValueError(SECRET_SUBJECT), "unexpected ValueError"),
        (RuntimeError(SECRET_SUBJECT), "unexpected RuntimeError"),
    ],
    ids=[
        "http",
        "os",
        "transport",
        "permission",
        "offline",
        "incomplete-read",
        "other-value-error",
        "safety-net",
    ],
)
def test_runtime_errors_log_type_only(built, store, fake_service, log_messages, error, expected):
    before = store.path.read_bytes()
    fake_service.sync.side_effect = error

    assert main([]) == 1

    assert expected in log_messages[-1]
    logged = "\n".join(log_messages)
    assert "Traceback" not in logged
    assert SECRET_SUBJECT not in logged and SECRET_BODY not in logged
    assert store.path.read_bytes() == before


def test_refresh_error_during_sync_returns_one_and_says_make_auth(
    built, store, fake_service, log_messages
):
    before = store.path.read_bytes()
    fake_service.sync.side_effect = RefreshError(SECRET_BODY)

    assert main([]) == 1

    assert "rejected or revoked" in log_messages[-1]
    assert "make auth" in log_messages[-1]
    assert all(SECRET_BODY not in message for message in log_messages)
    assert store.path.read_bytes() == before


def test_keyboard_interrupt_returns_130_and_leaves_state(built, store, fake_service, log_messages):
    before = store.path.read_bytes()
    fake_service.sync.side_effect = KeyboardInterrupt

    assert main([]) == 130

    assert "interrupted; state not advanced" in log_messages[-1]
    assert store.path.read_bytes() == before


def test_validation_error_during_sync_logs_type_only(built, fake_service, log_messages):
    # A ValidationError is a ValueError; its text echoes input values, so it must not be logged.
    with pytest.raises(ValidationError) as invalid:
        SyncState(history_id="")
    fake_service.sync.side_effect = invalid.value

    assert main([]) == 1

    assert "ValidationError" in log_messages[-1]
    assert "history_id" not in log_messages[-1]


def test_save_failure_returns_one_with_type_only(built, store, log_messages):
    with patch.object(store, "save", side_effect=OSError(SECRET_SUBJECT)):
        assert main([]) == 1

    assert "OSError" in log_messages[-1]
    assert all(SECRET_SUBJECT not in message for message in log_messages)


def test_help_exits_without_building_anything(capsys):
    with (
        patch(f"{MODULE}.get_settings") as settings,
        patch(f"{MODULE}._build") as build,
        pytest.raises(SystemExit) as exc,
    ):
        main(["--help"])

    assert exc.value.code == 0
    assert "email_summarizer.cli.sync" in capsys.readouterr().out
    settings.assert_not_called()
    build.assert_not_called()


def test_invalid_settings_return_one_with_message(log_messages):
    with pytest.raises(ValidationError) as invalid:
        GmailSettings(newsletter_senders=["-evil@example.com"])

    with (
        patch(f"{MODULE}.get_settings", side_effect=invalid.value),
        patch(f"{MODULE}._build") as build,
    ):
        assert main([]) == 1

    assert "newsletter_senders: value_error" in log_messages[-1]
    assert all("example.com" not in message for message in log_messages)
    build.assert_not_called()


def test_missing_env_file_returns_one_with_message(log_messages):
    error = FileNotFoundError("EMAIL_SUMMARIZER_ENV_FILE points to a missing file: /x/.env")
    with patch(f"{MODULE}.get_settings", side_effect=error):
        assert main([]) == 1

    assert "EMAIL_SUMMARIZER_ENV_FILE" in log_messages[-1]
