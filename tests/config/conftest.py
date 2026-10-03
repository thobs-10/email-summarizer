"""Fixtures and test data for the settings tests.

Scoped to tests/config so the environment isolation below only applies to settings tests.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from email_summarizer.config.settings import ENV_FILE_VAR, AppSettings, get_settings

# ----------------------------------
# environment helpers
# ----------------------------------


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch) -> Iterator[None]:
    """Drop GMAIL / GMAIL__* / env-file overrides from the developer's shell and reset the cache."""
    for name in list(os.environ):
        if name == "GMAIL" or name.startswith("GMAIL__") or name == ENV_FILE_VAR:
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def set_gmail_env(monkeypatch) -> Callable[..., None]:
    """Set GMAIL__<FIELD> variables by field name, e.g. set_gmail_env(days_back="30")."""

    def _set(**fields: str) -> None:
        for field, value in fields.items():
            monkeypatch.setenv(f"GMAIL__{field.upper()}", value)

    return _set


@pytest.fixture
def load_settings(tmp_path, monkeypatch) -> Callable[[], AppSettings]:
    """Build AppSettings from the environment only (no .env), with cwd isolated to tmp_path."""
    monkeypatch.chdir(tmp_path)

    def _load() -> AppSettings:
        return AppSettings(_env_file=None)

    return _load


@pytest.fixture
def default_token_path(tmp_path) -> Path:
    """Where token_path lands when nothing overrides it (cwd is tmp_path in load_settings)."""
    return tmp_path.resolve() / "secrets" / "token.json"


@pytest.fixture
def home_dir(tmp_path, monkeypatch) -> Path:
    """Point HOME at a temp directory so "~" expansion is deterministic."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def project_env_file(tmp_path, monkeypatch) -> Path:
    """A project .env selected via ENV_FILE_VAR while cwd is a different directory."""
    project = tmp_path / "project"
    project.mkdir()
    env_file = project / ".env"
    env_file.write_text(f"GMAIL__BASE_DIR={project}\nGMAIL__DAYS_BACK=30\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv(ENV_FILE_VAR, str(env_file))
    return env_file


# ----------------------------------
# sender allow-list test data
# ----------------------------------


@pytest.fixture(params=["", "[]", " , "])
def empty_senders_raw(request) -> str:
    """Env values that should all produce an empty allow-list."""
    return request.param


@pytest.fixture(
    params=[
        ('"a@x.com"', ["a@x.com"]),
        ('"a@x.com","b@y.org"', ["a@x.com", "b@y.org"]),
        ("'a@x.com', 'b@y.org'", ["a@x.com", "b@y.org"]),
    ],
    ids=["single-double-quoted", "multi-double-quoted", "multi-single-quoted"],
)
def quoted_senders(request) -> tuple[str, list[str]]:
    """(raw env value, expected allow-list) for quoted sender values."""
    return request.param


@pytest.fixture(
    params=[
        "not-an-email",
        "@substack.com",
        "a@b",
        "a b@x.com",
        "Name <a@x.com>",
        "<a@x.com>",
        "a@x.com.",
        "a@.x.com",
        "a@x..com",
        ".a@x.com",
        "a..b@x.com",
        "a@-x.com",
        "a@x.c",
        "ü@exämple.com",
        "a@x.com;b@y.org",
    ]
)
def invalid_sender(request) -> str:
    """Values that are not exact, matchable sender addresses."""
    return request.param


@pytest.fixture(params=["first.last+tag@sub.example.co.uk", "a@xn--80ak6aa92e.com"])
def valid_sender_edge_case(request) -> str:
    """Unusual but valid addresses: plus-tags, subdomains, punycode domains."""
    return request.param


# ----------------------------------
# numeric bounds test data
# ----------------------------------


@pytest.fixture(
    params=[
        ("days_back", "0"),
        ("days_back", "91"),
        ("max_results", "0"),
        ("max_results", "501"),
        ("max_retries", "-1"),
        ("max_retries", "11"),
    ],
    ids=lambda p: f"{p[0]}={p[1]}",
)
def out_of_range_setting(request) -> tuple[str, str]:
    """(field name, value) pairs just outside each numeric field's allowed range."""
    return request.param
