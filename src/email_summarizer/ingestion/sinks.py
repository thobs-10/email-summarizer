"""Where the sync sends each fetched email."""

from pathlib import Path
from typing import Protocol

from email_summarizer.models.raw_email import MESSAGE_ID_PATTERN, RawEmail
from email_summarizer.utils.files import write_text_atomic


class EmailSink(Protocol):
    """Destination for synced emails (JSON files in step 7, SQL in Phase 2)."""

    def write(self, email: RawEmail) -> None:
        """Store one email.

        Must be idempotent by email.message_id: syncs overlap on purpose, so writing the
        same message twice must be harmless. Raise on failure; the sync then stops and
        does not advance its state, so nothing is skipped.
        """
        ...


class JsonFileSink:
    """Write each email to a JSON file in a directory, one file per email."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def write(self, email: RawEmail) -> None:
        """Write one email to `<message_id>.json`, atomically; rewriting an id overwrites it.

        Raises:
            ValueError: If the message id isn't plain alphanumeric, so it can't escape the
                directory (e.g. "../x") or name a hidden temp file.
            OSError: If the directory or file can't be written.
        """
        if not MESSAGE_ID_PATTERN.fullmatch(email.message_id):
            raise ValueError("message id is not a plain alphanumeric Gmail id")  # never echo it
        path = self.directory / f"{email.message_id}.json"
        write_text_atomic(path, email.model_dump_json(indent=2))
