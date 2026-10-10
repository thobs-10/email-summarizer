"""Where the sync sends each fetched email."""

from typing import Protocol

from email_summarizer.models.raw_email import RawEmail


class EmailSink(Protocol):
    """Destination for synced emails (JSON files in step 7, SQL in Phase 2)."""

    def write(self, email: RawEmail) -> None:
        """Store one email.

        Must be idempotent by email.message_id: syncs overlap on purpose, so writing the
        same message twice must be harmless. Raise on failure; the sync then stops and
        does not advance its state, so nothing is skipped.
        """
        ...
