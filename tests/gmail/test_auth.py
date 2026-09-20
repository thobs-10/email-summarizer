"""Tests for Gmail authentication (no network, no browser, no real token files)."""

import stat
import sys
from unittest.mock import patch

import pytest
from google.auth.exceptions import RefreshError

from email_summarizer.gmail import auth
from email_summarizer.gmail.auth import SCOPES, get_credentials

MODULE = "email_summarizer.gmail.auth"


def test_valid_token_is_returned_without_refresh_or_login(get_app_settings, token_path, make_creds):
    token_path.write_text("{}")  # _load_token only checks that the file exists
    creds = make_creds(valid=True)

    with (
        patch(f"{MODULE}.Credentials.from_authorized_user_file", return_value=creds) as load,
        patch(f"{MODULE}.InstalledAppFlow") as flow,
    ):
        result = get_credentials(get_app_settings)

    assert result is creds
    load.assert_called_once_with(token_path, SCOPES)
    creds.refresh.assert_not_called()
    flow.from_client_secrets_file.assert_not_called()


def test_expired_token_is_refreshed_and_saved(get_app_settings, token_path, make_creds):
    token_path.write_text("{}")
    creds = make_creds(valid=False, expired=True)

    with (
        patch(f"{MODULE}.Credentials.from_authorized_user_file", return_value=creds),
        patch(f"{MODULE}.Request"),
        patch(f"{MODULE}.InstalledAppFlow") as flow,
    ):
        result = get_credentials(get_app_settings)

    assert result is creds
    creds.refresh.assert_called_once()
    flow.from_client_secrets_file.assert_not_called()
    assert token_path.read_text() == '{"token": "fake"}'  # refreshed token persisted


def test_failed_refresh_falls_back_to_interactive_login(get_app_settings, token_path, make_creds):
    token_path.write_text("{}")
    stale = make_creds(valid=False, expired=True)
    stale.refresh.side_effect = RefreshError("token revoked")
    fresh = make_creds(valid=True)

    with (
        patch(f"{MODULE}.Credentials.from_authorized_user_file", return_value=stale),
        patch(f"{MODULE}.Request"),
        patch(f"{MODULE}.InstalledAppFlow") as flow,
    ):
        flow.from_client_secrets_file.return_value.run_local_server.return_value = fresh
        result = get_credentials(get_app_settings)

    assert result is fresh
    flow.from_client_secrets_file.assert_called_once_with(
        get_app_settings.client_secret_file, SCOPES
    )
    assert token_path.exists()  # the new token was saved


def test_no_token_file_runs_interactive_login(get_app_settings, token_path, make_creds):
    assert not token_path.exists()
    fresh = make_creds(valid=True)

    with patch(f"{MODULE}.InstalledAppFlow") as flow:
        server = flow.from_client_secrets_file.return_value.run_local_server
        server.return_value = fresh
        result = get_credentials(get_app_settings)

    assert result is fresh
    server.assert_called_once_with(port=0)
    assert token_path.read_text() == '{"token": "fake"}'


def test_expired_token_without_refresh_token_requires_login(
    get_app_settings, token_path, make_creds
):
    token_path.write_text("{}")
    stale = make_creds(valid=False, expired=True, refresh_token=None)
    fresh = make_creds(valid=True)

    with (
        patch(f"{MODULE}.Credentials.from_authorized_user_file", return_value=stale),
        patch(f"{MODULE}.InstalledAppFlow") as flow,
    ):
        flow.from_client_secrets_file.return_value.run_local_server.return_value = fresh
        result = get_credentials(get_app_settings)

    stale.refresh.assert_not_called()
    assert result is fresh


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_saved_token_is_owner_only(token_path, make_creds):
    auth._save_token(make_creds())
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_scope_is_read_only():
    assert SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]
