"""Tests for GmailSettings loading and validation through AppSettings.

Fixtures and test data live in tests/config/conftest.py.
"""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from email_summarizer.config.settings import ENV_FILE_VAR, GmailSettings, get_settings

# ----------------------------------
# defaults
# ----------------------------------


def test_defaults(load_settings, tmp_path, default_token_path):
    gmail = load_settings().gmail

    assert gmail.newsletter_senders == []
    assert gmail.days_back == 7
    assert gmail.max_results == 100
    assert gmail.max_retries == 3
    assert gmail.base_dir == tmp_path.resolve()
    assert gmail.token_path == default_token_path
    assert gmail.client_secret_file == tmp_path.resolve() / "secrets" / "credentials.json"


# ----------------------------------
# sender allow-list
# ----------------------------------


def test_senders_from_comma_separated_env(load_settings, set_gmail_env):
    set_gmail_env(newsletter_senders="a@x.com,b@y.org")

    assert load_settings().gmail.newsletter_senders == ["a@x.com", "b@y.org"]


def test_senders_from_json_env(load_settings, set_gmail_env):
    set_gmail_env(newsletter_senders='["a@x.com", "b@y.org"]')

    assert load_settings().gmail.newsletter_senders == ["a@x.com", "b@y.org"]


def test_empty_senders_env(load_settings, set_gmail_env, empty_senders_raw):
    set_gmail_env(newsletter_senders=empty_senders_raw)

    assert load_settings().gmail.newsletter_senders == []


def test_senders_are_normalised_and_deduplicated(load_settings, set_gmail_env):
    set_gmail_env(newsletter_senders=" News@X.com , news@x.com,b@y.org,")

    assert load_settings().gmail.newsletter_senders == ["news@x.com", "b@y.org"]


def test_quoted_senders_are_unquoted(load_settings, set_gmail_env, quoted_senders):
    raw, expected = quoted_senders
    set_gmail_env(newsletter_senders=raw)

    assert load_settings().gmail.newsletter_senders == expected


def test_invalid_sender_is_rejected(load_settings, set_gmail_env, invalid_sender):
    set_gmail_env(newsletter_senders=invalid_sender)

    with pytest.raises(ValidationError, match="newsletter_senders"):
        load_settings()


def test_valid_sender_edge_cases(load_settings, set_gmail_env, valid_sender_edge_case):
    set_gmail_env(newsletter_senders=valid_sender_edge_case)

    assert load_settings().gmail.newsletter_senders == [valid_sender_edge_case]


def test_malformed_json_senders_raise_validation_error(load_settings, set_gmail_env):
    set_gmail_env(newsletter_senders="[a@x.com")

    with pytest.raises(ValidationError, match="invalid JSON list"):
        load_settings()


def test_non_string_json_senders_are_rejected(load_settings, set_gmail_env):
    set_gmail_env(newsletter_senders="[1, 2]")

    with pytest.raises(ValidationError, match="newsletter_senders"):
        load_settings()


# ----------------------------------
# numeric bounds
# ----------------------------------


def test_numeric_out_of_range_is_rejected(load_settings, set_gmail_env, out_of_range_setting):
    field, value = out_of_range_setting
    set_gmail_env(**{field: value})

    with pytest.raises(ValidationError, match=field):
        load_settings()


# ----------------------------------
# path resolution
# ----------------------------------


def test_relative_paths_resolve_against_base_dir(load_settings, set_gmail_env, tmp_path):
    base = tmp_path / "project"
    set_gmail_env(base_dir=str(base), token_path="secrets/my_token.json")

    assert load_settings().gmail.token_path == base.resolve() / "secrets" / "my_token.json"


def test_base_dir_expands_home(load_settings, set_gmail_env, home_dir):
    set_gmail_env(base_dir="~/project")

    gmail = load_settings().gmail

    assert gmail.base_dir == (home_dir / "project").resolve()
    assert gmail.raw_output_dir == gmail.base_dir / "data" / "raw"


def test_relative_base_dir_is_rejected(load_settings, set_gmail_env):
    set_gmail_env(base_dir="relative/dir")

    with pytest.raises(ValidationError, match="absolute"):
        load_settings()


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_path_env_uses_default(
    load_settings, set_gmail_env, tmp_path, default_token_path, blank
):
    set_gmail_env(token_path=blank, base_dir=blank)

    gmail = load_settings().gmail

    assert gmail.base_dir == tmp_path.resolve()
    assert gmail.token_path == default_token_path


def test_absolute_and_home_paths(load_settings, set_gmail_env, home_dir, tmp_path):
    absolute = tmp_path / "abs" / ".." / "abs" / "state.json"
    set_gmail_env(token_path="~/token.json", state_path=str(absolute))

    gmail = load_settings().gmail

    assert gmail.token_path == home_dir.resolve() / "token.json"
    assert gmail.state_path == (tmp_path / "abs" / "state.json").resolve()


def test_explicit_none_base_dir_falls_back_to_cwd(tmp_path, monkeypatch, default_token_path):
    # Only an explicit None reaches _require_absolute_base_dir's None branch (a blank env value
    # becomes the default, and field validators don't run on defaults). _resolve_paths then
    # replaces None with the cwd, so base_dir is never None after validation.
    monkeypatch.chdir(tmp_path)

    gmail = GmailSettings(base_dir=None)

    assert gmail.base_dir == tmp_path.resolve()
    assert gmail.token_path == default_token_path


# ----------------------------------
# environment variable sources
# ----------------------------------


def test_unprefixed_env_vars_are_ignored(load_settings, monkeypatch, default_token_path):
    # Guards against nesting GmailSettings as BaseSettings, which would read TOKEN_PATH directly.
    monkeypatch.setenv("TOKEN_PATH", "/should/not/be/used.json")

    assert load_settings().gmail.token_path == default_token_path


def test_gmail_json_env_var_is_cleared_by_isolation(load_settings, default_token_path):
    # pydantic-settings also accepts the whole nested model as JSON in GMAIL; isolated_env must
    # remove it so a developer's shell can't leak into these tests.
    assert "GMAIL" not in os.environ
    assert load_settings().gmail.token_path == default_token_path


def test_gmail_json_env_var_is_honoured(load_settings, monkeypatch, tmp_path):
    monkeypatch.setenv("GMAIL", '{"token_path": "t.json", "newsletter_senders": "a@x.com"}')

    gmail = load_settings().gmail

    assert gmail.token_path == tmp_path.resolve() / "t.json"
    assert gmail.newsletter_senders == ["a@x.com"]


# ----------------------------------
# model behaviour
# ----------------------------------


def test_gmail_settings_usable_standalone(tmp_path):
    gmail = GmailSettings(base_dir=tmp_path, newsletter_senders=["A@X.com"])

    assert gmail.newsletter_senders == ["a@x.com"]
    assert gmail.raw_output_dir == tmp_path.resolve() / "data" / "raw"


def test_gmail_settings_are_frozen(tmp_path):
    gmail = GmailSettings(base_dir=tmp_path)

    with pytest.raises(ValidationError, match="frozen"):
        gmail.token_path = Path("relative.json")


@pytest.mark.parametrize("mode", ["python", "json"])
def test_dump_round_trip_is_idempotent(tmp_path, mode):
    gmail = GmailSettings(base_dir=tmp_path, token_path="t.json", newsletter_senders=["a@x.com"])

    reloaded = GmailSettings.model_validate(gmail.model_dump(mode=mode))

    assert reloaded == gmail


# ----------------------------------
# get_settings and the env file
# ----------------------------------


def test_get_settings_loads_env_file_from_env_var(project_env_file):
    project = project_env_file.parent

    gmail = get_settings().gmail

    assert gmail.days_back == 30
    assert gmail.token_path == project.resolve() / "secrets" / "token.json"


def test_get_settings_defaults_to_cwd_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("GMAIL__DAYS_BACK=12\n")
    monkeypatch.chdir(tmp_path)

    assert get_settings().gmail.days_back == 12


def test_get_settings_missing_configured_env_file_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / "missing.env"))

    with pytest.raises(FileNotFoundError, match=ENV_FILE_VAR):
        get_settings()
