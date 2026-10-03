"""Tests for Gmail authentication (no network, no browser, no real token files)."""

import json
import os
import stat
import subprocess
import sys
from unittest.mock import patch

import pytest
from google.auth.exceptions import RefreshError

from email_summarizer.gmail import auth
from email_summarizer.gmail.auth import (
    SCOPES,
    GmailAuthError,
    load_credentials,
    run_interactive_flow,
)

MODULE = "email_summarizer.gmail.auth"


@pytest.fixture(autouse=True)
def flow():
    """Replace the browser flow everywhere so no test can ever open a browser."""
    with patch(f"{MODULE}.InstalledAppFlow") as flow:
        yield flow


@pytest.fixture
def stored(gmail_settings):
    """Write a placeholder token file and make loading it return the given fake credentials."""

    def _stored(creds):
        gmail_settings.token_path.parent.mkdir(parents=True, exist_ok=True)
        gmail_settings.token_path.write_text("{}")
        return patch(f"{MODULE}.Credentials.from_authorized_user_file", return_value=creds)

    return _stored


# ----------------------------------
# load_credentials
# ----------------------------------
def test_valid_token_is_returned_untouched(gmail_settings, stored, make_creds, flow):
    creds = make_creds(valid=True)

    with stored(creds) as load:
        assert load_credentials(gmail_settings) is creds

    load.assert_called_once_with(str(gmail_settings.token_path))
    creds.refresh.assert_not_called()
    assert gmail_settings.token_path.read_text() == "{}"  # not rewritten
    assert flow.mock_calls == []


def test_expired_token_is_refreshed_and_saved(gmail_settings, stored, make_creds, flow):
    creds = make_creds(valid=False, expired=True)

    with stored(creds):
        assert load_credentials(gmail_settings) is creds

    creds.refresh.assert_called_once()
    assert gmail_settings.token_path.read_text() == '{"token": "fake"}'
    assert flow.mock_calls == []


def test_refresh_error_raises_auth_error(gmail_settings, stored, make_creds, flow):
    creds = make_creds(valid=False, expired=True)
    creds.refresh.side_effect = RefreshError("token revoked")

    with stored(creds), pytest.raises(GmailAuthError, match="make auth") as exc:
        load_credentials(gmail_settings)

    assert isinstance(exc.value.__cause__, RefreshError)
    assert gmail_settings.token_path.read_text() == "{}"  # stale token left as is
    assert flow.mock_calls == []


def test_expired_token_without_refresh_token_raises(gmail_settings, stored, make_creds, flow):
    creds = make_creds(valid=False, expired=True, refresh_token=None)

    with stored(creds), pytest.raises(GmailAuthError, match="no refresh token"):
        load_credentials(gmail_settings)

    creds.refresh.assert_not_called()
    assert flow.mock_calls == []


def test_missing_token_raises(gmail_settings, flow):
    with pytest.raises(GmailAuthError, match="Cannot load") as exc:
        load_credentials(gmail_settings)

    assert isinstance(exc.value.__cause__, FileNotFoundError)
    assert flow.mock_calls == []


@pytest.mark.parametrize("content", ["not json", "{}", '{"client_id": "x"}'])
def test_corrupt_token_raises(gmail_settings, flow, content):
    gmail_settings.token_path.parent.mkdir(parents=True)
    gmail_settings.token_path.write_text(content)

    with pytest.raises(GmailAuthError, match="Cannot load") as exc:
        load_credentials(gmail_settings)

    assert isinstance(exc.value.__cause__, ValueError)
    assert flow.mock_calls == []


@pytest.mark.parametrize("scopes", [["https://www.googleapis.com/auth/gmail.labels"], None])
def test_token_without_readonly_scope_raises(gmail_settings, flow, scopes):
    # A real (unmocked) token file, so the scope check runs against what Google wrote.
    token = {"client_id": "id", "client_secret": "secret", "refresh_token": "refresh"}
    if scopes is not None:
        token["scopes"] = scopes
    gmail_settings.token_path.parent.mkdir(parents=True)
    gmail_settings.token_path.write_text(json.dumps(token))

    with pytest.raises(GmailAuthError, match="read-only scope"):
        load_credentials(gmail_settings)

    assert flow.mock_calls == []


# ----------------------------------
# run_interactive_flow
# ----------------------------------
def test_interactive_flow_uses_client_secret_and_saves_token(gmail_settings, make_creds, flow):
    gmail_settings.client_secret_file.parent.mkdir(parents=True)
    gmail_settings.client_secret_file.write_text("{}")  # never parsed: the flow is mocked
    fresh = make_creds()
    server = flow.from_client_secrets_file.return_value.run_local_server
    server.return_value = fresh

    assert run_interactive_flow(gmail_settings) is fresh

    flow.from_client_secrets_file.assert_called_once_with(
        str(gmail_settings.client_secret_file), SCOPES
    )
    server.assert_called_once_with(port=0)
    assert gmail_settings.token_path.read_text() == '{"token": "fake"}'


def test_interactive_flow_without_client_secret_fails_clearly(gmail_settings, flow):
    with pytest.raises(FileNotFoundError, match="client secret not found"):
        run_interactive_flow(gmail_settings)

    assert flow.mock_calls == []
    assert not gmail_settings.token_path.exists()


# ----------------------------------
# _save_token
# ----------------------------------
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_saved_token_is_owner_only(gmail_settings, make_creds):
    auth._save_token(make_creds(), gmail_settings.token_path)
    assert stat.S_IMODE(gmail_settings.token_path.stat().st_mode) == 0o600


def test_save_overwrites_and_leaves_no_temp_files(gmail_settings, make_creds):
    path = gmail_settings.token_path
    path.parent.mkdir(parents=True)
    path.write_text("old")

    auth._save_token(make_creds(), path)

    assert path.read_text() == '{"token": "fake"}'
    assert os.listdir(path.parent) == [path.name]


def test_failed_save_keeps_old_token_and_cleans_up(gmail_settings, make_creds):
    path = gmail_settings.token_path
    path.parent.mkdir(parents=True)
    path.write_text("old")
    creds = make_creds()
    creds.to_json.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        auth._save_token(creds, path)

    assert path.read_text() == "old"
    assert os.listdir(path.parent) == [path.name]


# ----------------------------------
# module hygiene
# ----------------------------------
def test_scope_is_read_only():
    assert SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_import_has_no_side_effects():
    # A fresh interpreter, so the check can't be masked by modules already imported here.
    code = (
        "import dotenv\n"
        "from email_summarizer.config import settings\n"
        "def boom(*args, **kwargs):\n"
        "    raise SystemExit('called at import time')\n"
        "dotenv.load_dotenv = settings.get_settings = boom\n"
        "import email_summarizer.gmail.auth\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
