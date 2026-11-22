"""Run the read-only Gmail MCP server over stdio. Run with ``make mcp``.

MCP clients should launch the module directly, since make can print to stdout:
``uv run --directory /abs/path/to/repo python -m email_summarizer.mcp_server``.

stdout carries MCP protocol messages only, so this module never prints. Logs go to stderr,
and a startup failure (bad settings, missing or expired token) exits non-zero with one
stderr line. Never opens a browser: sign in first with ``make auth``.
"""

import sys

from fastmcp import FastMCP
from google.auth.exceptions import TransportError
from loguru import logger
from pydantic import ValidationError

from email_summarizer.config.settings import (
    GmailSettings,
    describe_validation_error,
    get_settings,
)
from email_summarizer.gmail.auth import GmailAuthError, load_credentials
from email_summarizer.gmail.client import GmailClient
from email_summarizer.gmail.service import NewsletterGmailService
from email_summarizer.mcp_server.server import build_server
from email_summarizer.state.gmail_state import JsonStateStore

_EXIT_INTERRUPTED: int = 130


def configure_logging() -> None:
    """Route every loguru message to stderr, never stdout.

    diagnose=False and backtrace=False keep variable values (which could hold email
    content) out of any traceback loguru formats.
    """
    logger.remove()
    logger.add(sys.stderr, level="INFO", diagnose=False, backtrace=False)


def build_from_settings(settings: GmailSettings) -> FastMCP:
    """Wire credentials, Gmail client, service and state store into the server.

    Raises:
        GmailAuthError: If the saved token is missing or unusable.
        TransportError: If an expired token can't be refreshed for lack of network.
        OSError: If a refreshed token can't be saved.
    """
    creds = load_credentials(settings)  # never the browser flow
    service = NewsletterGmailService(GmailClient(creds, settings.max_retries), settings)
    return build_server(service, JsonStateStore(settings.state_path), settings)


def main() -> int:
    """Start the stdio MCP server; return a process exit code when it stops or can't start."""
    configure_logging()
    settings = _load_settings()
    if settings is None:
        return 1
    try:
        mcp = build_from_settings(settings)
    except GmailAuthError as exc:  # names the token path and says to run `make auth`
        logger.error("MCP server not started: {}", exc)
        return 1
    except (TransportError, OSError) as exc:  # type only: the text may carry internals
        logger.error("MCP server not started: Gmail token refresh failed ({}).", type(exc).__name__)
        return 1
    if not settings.newsletter_senders:
        logger.warning("GMAIL__NEWSLETTER_SENDERS is empty; the tools will find nothing.")
    try:
        mcp.run(transport="stdio", show_banner=False)  # the banner must not touch stdout
    except KeyboardInterrupt:
        return _EXIT_INTERRUPTED
    return 0


def _load_settings() -> GmailSettings | None:
    """Load the Gmail settings, or log why they are invalid and return None."""
    try:
        return get_settings().gmail
    except FileNotFoundError as exc:  # EMAIL_SUMMARIZER_ENV_FILE names a missing file
        logger.error("MCP server not started, invalid settings: {}", exc)
    except ValidationError as exc:
        logger.error("MCP server not started, invalid settings: {}", describe_validation_error(exc))
    return None


if __name__ == "__main__":
    raise SystemExit(main())
