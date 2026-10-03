"""Tests for GmailSettings loading and validation through AppSettings."""

import pytest
from pydantic import ValidationError

from email_summarizer.config.settings import AppSettings, GmailSettings


@pytest.fixture
def load_settings(tmp_path, monkeypatch):
    """Build AppSettings from the environment only (no .env), with cwd isolated to tmp_path."""
    monkeypatch.chdir(tmp_path)

    def _load() -> AppSettings:
        return AppSettings(_env_file=None)

    return _load


def test_defaults(load_settings, tmp_path):
    gmail = load_settings().gmail

    assert gmail.newsletter_senders == []
    assert gmail.days_back == 7
    assert gmail.max_results == 100
    assert gmail.max_retries == 3
    assert gmail.token_path == tmp_path.resolve() / "secrets" / "token.json"
    assert gmail.client_secret_file == tmp_path.resolve() / "secrets" / "credentials.json"


def test_senders_from_comma_separated_env(load_settings, monkeypatch):
    monkeypatch.setenv("GMAIL__NEWSLETTER_SENDERS", "a@x.com,b@y.org")

    assert load_settings().gmail.newsletter_senders == ["a@x.com", "b@y.org"]


def test_senders_from_json_env(load_settings, monkeypatch):
    monkeypatch.setenv("GMAIL__NEWSLETTER_SENDERS", '["a@x.com", "b@y.org"]')

    assert load_settings().gmail.newsletter_senders == ["a@x.com", "b@y.org"]


def test_senders_are_normalised_and_deduplicated(load_settings, monkeypatch):
    monkeypatch.setenv("GMAIL__NEWSLETTER_SENDERS", " News@X.com , news@x.com,b@y.org,")

    assert load_settings().gmail.newsletter_senders == ["news@x.com", "b@y.org"]


@pytest.mark.parametrize("bad_sender", ["not-an-email", "@substack.com", "a@b", "a b@x.com"])
def test_invalid_sender_is_rejected(load_settings, monkeypatch, bad_sender):
    monkeypatch.setenv("GMAIL__NEWSLETTER_SENDERS", bad_sender)

    with pytest.raises(ValidationError, match="newsletter_senders"):
        load_settings()


@pytest.mark.parametrize("days_back", ["0", "91"])
def test_days_back_out_of_range_is_rejected(load_settings, monkeypatch, days_back):
    monkeypatch.setenv("GMAIL__DAYS_BACK", days_back)

    with pytest.raises(ValidationError, match="days_back"):
        load_settings()


def test_relative_paths_resolve_against_base_dir(load_settings, monkeypatch, tmp_path):
    base = tmp_path / "project"
    monkeypatch.setenv("GMAIL__BASE_DIR", str(base))
    monkeypatch.setenv("GMAIL__TOKEN_PATH", "secrets/my_token.json")

    assert load_settings().gmail.token_path == base.resolve() / "secrets" / "my_token.json"


def test_absolute_and_home_paths(load_settings, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GMAIL__TOKEN_PATH", "~/token.json")
    absolute = tmp_path / "abs" / "state.json"
    monkeypatch.setenv("GMAIL__STATE_PATH", str(absolute))

    gmail = load_settings().gmail

    assert gmail.token_path == (tmp_path / "home").resolve() / "token.json"
    assert gmail.state_path == absolute.resolve()


def test_unprefixed_env_vars_are_ignored(load_settings, monkeypatch, tmp_path):
    # Guards against nesting GmailSettings as BaseSettings, which would read TOKEN_PATH directly.
    monkeypatch.setenv("TOKEN_PATH", "/should/not/be/used.json")

    assert load_settings().gmail.token_path == tmp_path.resolve() / "secrets" / "token.json"


def test_gmail_settings_usable_standalone(tmp_path):
    gmail = GmailSettings(base_dir=tmp_path, newsletter_senders=["A@X.com"])

    assert gmail.newsletter_senders == ["a@x.com"]
    assert gmail.raw_output_dir == tmp_path.resolve() / "data" / "raw"
