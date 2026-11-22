"""Sync allow-listed newsletters from Gmail to data/raw. Run with ``make sync``."""

import argparse
from http.client import HTTPException
from pathlib import Path

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error
from loguru import logger
from pydantic import ValidationError

from email_summarizer.config.settings import (
    GmailSettings,
    describe_validation_error,
    get_settings,
)
from email_summarizer.gmail.auth import GmailAuthError, load_credentials
from email_summarizer.gmail.client import GmailClient
from email_summarizer.gmail.service import EmptyAllowListError, NewsletterGmailService
from email_summarizer.ingestion.sinks import EmailSink, JsonFileSink
from email_summarizer.models.gmail_views import SyncResult
from email_summarizer.state.gmail_state import JsonStateStore

# Expected failures whose text may carry email content (HttpError bodies, file paths,
# pydantic input values), so only their type is logged. HttpLib2Error (e.g. offline) and
# HTTPException (e.g. IncompleteRead) come raw from the transport, not as TransportError.
_TYPE_ONLY_ERRORS = (
    HttpError,
    HttpLib2Error,
    HTTPException,
    TransportError,
    OSError,
    ValidationError,
)

_EXIT_INTERRUPTED: int = 130


class StateFileError(Exception):
    """The sync state file is corrupt; the message names the path, never the contents."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Sync state file {path} is corrupt. Delete it to re-sync from days_back.")


def run_sync(service: NewsletterGmailService, sink: EmailSink, store: JsonStateStore) -> SyncResult:
    """Sync new newsletters into the sink, then save the new state.

    The state is saved only after sync returns: if it raises, the old state stays on disk
    and the next run retries from there.

    Args:
        service: The Gmail service to fetch new messages.
        sink: Where to write each fetched email.
        store: Where to load and save the sync state.

    Raises:
        StateFileError: If the state file is corrupt.
        Exception: Whatever service.sync or store.save raises (e.g. HttpError, OSError).

    Returns:
        result: The result of the sync, including the new state.
    """
    try:
        state = store.load()
    except ValidationError as exc:  # its text would echo the file contents, so drop it
        raise StateFileError(store.path) from exc
    result = service.sync(state, sink)
    store.save(result.new_state)
    return result


def _build(
    settings: GmailSettings,
) -> tuple[NewsletterGmailService, JsonFileSink, JsonStateStore]:
    """Wire the service, sink and state store from settings. Never opens a browser.

    Raises:
        GmailAuthError: If the saved token is missing or unusable.
        TransportError: If an expired token can't be refreshed for lack of network.
        OSError: If a refreshed token can't be saved.
    """
    creds = load_credentials(settings)
    client = GmailClient(creds, settings.max_retries)
    service = NewsletterGmailService(client, settings)
    return service, JsonFileSink(settings.raw_output_dir), JsonStateStore(settings.state_path)


def _describe(exc: BaseException) -> str:
    """Error type (plus HTTP status for HttpError), never the message or body."""
    name = type(exc).__name__
    status = getattr(getattr(exc, "resp", None), "status", None)
    return f"{name} (HTTP {status})" if isinstance(exc, HttpError) and status else name


def main(argv: list[str] | None = None) -> int:
    """Sync new newsletters and save the sync state. Returns a process exit code."""
    argparse.ArgumentParser(
        prog="python -m email_summarizer.cli.sync",
        description="Fetch allow-listed newsletters added since the last run (or the last "
        "GMAIL__DAYS_BACK days on the first run) into GMAIL__RAW_OUTPUT_DIR.",
    ).parse_args(argv)
    try:
        settings = get_settings().gmail
    except FileNotFoundError as exc:
        logger.error("Gmail sync failed, invalid settings: {}", exc)
        return 1
    except ValidationError as exc:
        logger.error("Gmail sync failed, invalid settings: {}", describe_validation_error(exc))
        return 1
    return _sync(settings)


def _sync(settings: GmailSettings) -> int:
    """Build, sync and save, mapping each failure to one log line. Returns an exit code."""

    try:
        result = run_sync(*_build(settings))
    except (GmailAuthError, StateFileError, EmptyAllowListError) as exc:
        logger.error("Gmail sync failed: {}", exc)
        return 1
    except RefreshError:
        logger.error(
            "Gmail sync failed: the token was rejected or revoked. "
            "Run `make auth` to sign in again."
        )
        return 1
    except _TYPE_ONLY_ERRORS as exc:
        logger.error("Gmail sync failed ({}); state not advanced.", _describe(exc))
        return 1
    except Exception as exc:  # safety net: an unexpected error's text may hold email content
        logger.error("Gmail sync failed (unexpected {}); state not advanced.", type(exc).__name__)
        return 1
    except KeyboardInterrupt:
        logger.error("Gmail sync interrupted; state not advanced")
        return _EXIT_INTERRUPTED
    _log_summary(result, settings.state_path)
    return 0


def _log_summary(result: SyncResult, state_path: Path) -> None:
    """Log one content-free INFO line for a successful sync."""
    logger.info(
        "Gmail sync done: mode={} written={} candidates={} history_fallback={}; state saved to {}",
        result.mode,
        result.written,
        result.candidates,
        result.history_fallback,
        state_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
