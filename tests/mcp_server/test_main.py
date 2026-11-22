"""Tests for the MCP server entry point (python -m email_summarizer.mcp_server).

Gmail, the token and the stdio loop are faked; nothing here opens a browser or the network.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from google.auth.exceptions import TransportError
from loguru import logger
from pydantic import ValidationError

from email_summarizer.config.settings import ENV_FILE_VAR, AppSettings, GmailSettings
from email_summarizer.gmail.auth import GmailAuthError
from email_summarizer.mcp_server import __main__ as entry

MODULE = "email_summarizer.mcp_server.__main__"
SENDERS = ["news@example.com"]


@pytest.fixture(autouse=True)
def restore_loguru() -> Iterator[None]:
    """main() replaces loguru's handlers with one bound to the (captured) stderr; undo it."""
    yield
    logger.remove()
    logger.add(sys.stderr)


@pytest.fixture
def settings(tmp_path: Path) -> GmailSettings:
    """Settings in tmp_path, so there is no token there."""
    return GmailSettings(base_dir=tmp_path, newsletter_senders=SENDERS)


@pytest.fixture
def app_settings(settings: GmailSettings) -> Iterator[AppSettings]:
    """Patch get_settings so main() never reads the developer's .env."""
    app = AppSettings(gmail=settings, _env_file=None)  # type: ignore[call-arg]
    with patch(f"{MODULE}.get_settings", return_value=app):
        yield app


@pytest.fixture
def wired(app_settings: AppSettings) -> Iterator[MagicMock]:
    """Fake credentials and Gmail client, and a stdio loop that returns at once."""
    with (
        patch(f"{MODULE}.load_credentials", return_value=MagicMock(name="creds")),
        patch(f"{MODULE}.GmailClient"),
        patch.object(FastMCP, "run") as run,
    ):
        yield run


# ----------------------------------
# startup failures: non-zero exit, one stderr message, nothing on stdout
# ----------------------------------
def test_missing_token_exits_non_zero_with_stderr_message(app_settings, capsys):
    assert entry.main() == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert "MCP server not started" in err and "make auth" in err


def test_auth_error_never_starts_the_server(app_settings, capsys):
    with (
        patch(f"{MODULE}.load_credentials", side_effect=GmailAuthError("Token expired.")),
        patch.object(FastMCP, "run") as run,
    ):
        assert entry.main() == 1

    run.assert_not_called()
    assert capsys.readouterr().out == ""


def test_refresh_network_failure_logs_type_only(app_settings, capsys):
    error = TransportError("connection to oauth2.googleapis.com failed: secret detail")
    with patch(f"{MODULE}.load_credentials", side_effect=error):
        assert entry.main() == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert "TransportError" in err and "secret detail" not in err


def test_invalid_settings_exit_non_zero_without_echoing_values(capsys):
    with pytest.raises(ValidationError) as excinfo:
        GmailSettings(newsletter_senders=["Bank Alerts <alerts@bank.example>"])
    with patch(f"{MODULE}.get_settings", side_effect=excinfo.value):
        assert entry.main() == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert "invalid settings" in err and "newsletter_senders" in err
    assert "bank.example" not in err


def test_missing_env_file_exits_non_zero(capsys):
    error = FileNotFoundError(f"{ENV_FILE_VAR} points to a missing file: /nowhere/.env")
    with patch(f"{MODULE}.get_settings", side_effect=error):
        assert entry.main() == 1

    out, err = capsys.readouterr()
    assert out == "" and ENV_FILE_VAR in err


# ----------------------------------
# successful startup
# ----------------------------------
def test_main_runs_stdio_without_banner(wired, capsys):
    assert entry.main() == 0

    wired.assert_called_once_with(transport="stdio", show_banner=False)
    assert capsys.readouterr().out == ""


def test_main_warns_when_allow_list_is_empty(wired, app_settings, capsys):
    app_settings.gmail = app_settings.gmail.model_copy(update={"newsletter_senders": []})

    assert entry.main() == 0

    assert "GMAIL__NEWSLETTER_SENDERS is empty" in capsys.readouterr().err


def test_main_returns_130_on_ctrl_c(wired):
    wired.side_effect = KeyboardInterrupt

    assert entry.main() == 130


@pytest.mark.anyio
async def test_build_from_settings_wires_real_service_and_tools(settings):
    creds = MagicMock(name="creds")
    with (
        patch(f"{MODULE}.load_credentials", return_value=creds) as load,
        patch(f"{MODULE}.GmailClient") as gmail_client,
    ):
        mcp = entry.build_from_settings(settings)

    load.assert_called_once_with(settings)
    gmail_client.assert_called_once_with(creds, settings.max_retries)
    async with Client(mcp) as client:
        assert len(await client.list_tools()) == 4


def test_configure_logging_sends_everything_to_stderr(capsys):
    entry.configure_logging()
    logger.info("hello from the server")

    out, err = capsys.readouterr()
    assert out == "" and "hello from the server" in err


# ----------------------------------
# real process: what an MCP client actually sees
# ----------------------------------
def _isolated_env(tmp_path: Path) -> dict[str, str]:
    """The current env minus app and FastMCP settings, pointed at an empty .env in tmp_path."""
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    prefixes = ("GMAIL__", "RUN__", "FASTMCP_", ENV_FILE_VAR)
    env = {key: value for key, value in os.environ.items() if not key.startswith(prefixes)}
    env |= {
        "PYTHONPATH": os.pathsep.join(sys.path),
        ENV_FILE_VAR: str(env_file),
        "GMAIL__BASE_DIR": str(tmp_path),
    }
    return env


def test_process_without_token_writes_nothing_to_stdout(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "email_summarizer.mcp_server"],
        cwd=tmp_path,
        env=_isolated_env(tmp_path),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "make auth" in result.stderr


def test_import_has_no_side_effects(tmp_path):
    # A fresh interpreter, so the check can't be masked by modules already imported here.
    code = (
        "from email_summarizer.config import settings\n"
        "from email_summarizer.gmail import auth\n"
        "def boom(*args, **kwargs):\n"
        "    raise SystemExit('called at import time')\n"
        "settings.get_settings = auth.load_credentials = boom\n"
        "import email_summarizer.mcp_server\n"
        "import email_summarizer.mcp_server.server\n"
        "import email_summarizer.mcp_server.__main__\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=_isolated_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
