"""Tests for the auth CLI run by `make auth` (the OAuth flow is never really run)."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from email_summarizer.cli.auth import main
from email_summarizer.config.settings import AppSettings, GmailSettings

MODULE = "email_summarizer.cli.auth"


@pytest.fixture
def app_settings(gmail_settings):
    """Patch get_settings so the CLI never reads the developer's .env."""
    settings = AppSettings(gmail=gmail_settings, _env_file=None)
    with patch(f"{MODULE}.get_settings", return_value=settings):
        yield settings


def test_success_returns_zero_and_logs_token_path(app_settings, log_messages):
    with patch(f"{MODULE}.run_interactive_flow") as flow:
        assert main([]) == 0

    flow.assert_called_once_with(app_settings.gmail)
    assert str(app_settings.gmail.token_path) in log_messages[-1]


def test_missing_client_secret_returns_one_with_message(app_settings, log_messages):
    # Real run_interactive_flow: it fails on the missing secret before any browser opens.
    assert main([]) == 1

    assert "client secret not found" in log_messages[-1]
    assert not app_settings.gmail.token_path.exists()


def test_help_exits_without_running_the_flow(capsys):
    with patch(f"{MODULE}.run_interactive_flow") as flow, pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    assert "email_summarizer.cli.auth" in capsys.readouterr().out
    flow.assert_not_called()


def test_invalid_settings_return_one_with_message(log_messages):
    with pytest.raises(ValidationError) as invalid:
        GmailSettings(newsletter_senders=["not-an-email"])

    with patch(f"{MODULE}.get_settings", side_effect=invalid.value):
        assert main([]) == 1

    assert "newsletter_senders" in log_messages[-1]
