"""NewsletterGmailService: the only component that knows how Gmail works. Synchronous by design.

The ingestion pipeline calls it directly; the MCP server is a thin adapter over it. Every
result is re-checked against the sender allow-list after parsing, because Gmail's "from:"
search also matches display names. Logs carry message ids and content-free reasons only.
"""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import Any, Protocol

from loguru import logger

from email_summarizer.config.settings import GmailSettings
from email_summarizer.gmail.parser import MessageParseError, parse_message
from email_summarizer.gmail.queries import build_sender_queries
from email_summarizer.models.gmail_views import NewsletterRef
from email_summarizer.models.raw_email import RawEmail

# find() only lists metadata, so it asks Gmail for these headers and no bodies.
_REF_HEADERS = ["From", "Subject"]

# Gmail message ids are short alphanumeric strings. fetch() takes ids from callers such as
# an LLM tool call; anything else would get a 400 from Gmail, so reject it first.
_MESSAGE_ID = re.compile(r"[A-Za-z0-9]{1,64}")


class GmailReader(Protocol):
    """The GmailClient methods the service uses; GmailClient and test fakes both match it."""

    def get_message(
        self, message_id: str, fmt: str = "full", metadata_headers: list[str] | None = None
    ) -> dict[str, Any] | None: ...

    def get_message_ids(self, query: str) -> Iterator[str]: ...

    def get_current_history_id(self) -> str: ...

    def list_added_message_ids(self, start_history_id: str) -> tuple[list[str], str]: ...


def _utc_now() -> datetime:
    """Current time as an aware UTC datetime (the default clock)."""
    return datetime.now(UTC)


class NewsletterGmailService:
    """Read allow-listed newsletters from Gmail: check senders, fetch one, find recent ones."""

    def __init__(
        self,
        client: GmailReader,
        settings: GmailSettings,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._allowed = frozenset(settings.newsletter_senders)  # already lower-cased
        self._now = now

    def is_allowed(self, sender_email: str) -> bool:
        """Return True if the address is on the allow-list (exact, case-insensitive match).
        Args:
            sender_email: The email address to check.

        Returns:
            True if the address is on the allow-list, False otherwise.
        """
        return sender_email.lower() in self._allowed

    def fetch(self, message_id: str) -> RawEmail | None:
        """Return the full newsletter, or None if it is missing, unparseable or not allow-listed.

        These cases, and a malformed id, all look the same to the caller on purpose, so a
        caller holding an arbitrary id (e.g. an LLM tool call) can't learn whether some other
        message exists. Other Gmail errors (e.g. HttpError) propagate.
        """
        if not _MESSAGE_ID.fullmatch(message_id):
            _log_skip("<invalid>", "malformed message id")  # never echo the caller's value
            return None
        try:
            email = self._fetch_full(message_id)
        except MessageParseError as exc:
            _log_skip(message_id, str(exc))
            return None
        if email is None:
            _log_skip(message_id, "not found")
            return None
        if not self.is_allowed(email.sender_email):
            _log_skip(message_id, "sender not allow-listed")
            return None
        return email

    def find(self, sender: str | None, days: int, max_results: int) -> list[NewsletterRef]:
        """List allow-listed newsletters from the last `days` days, newest first.

        Args:
            sender: Limit to this allow-listed address; None searches the whole allow-list.
            days: How far back to look; at least 1.
            max_results: Maximum number of refs to return; at least 1. Fewer may come back
                even when more exist: skipped (spoofed, deleted or unparseable) ids count
                toward the per-query cap, which keeps Gmail calls bounded.

        Raises:
            ValueError: If days or max_results is below 1, the sender is not allow-listed,
                or the allow-list is empty.
        Returns:
            A list of allow-listed newsletter references, newest first, up to `max_results`.

        """
        if days < 1 or max_results < 1:
            raise ValueError("days and max_results must both be at least 1")
        senders = self._senders_to_search(sender)
        queries = build_sender_queries(senders, after=self._now() - timedelta(days=days))
        seen: set[str] = set()
        refs: list[NewsletterRef] = []
        for query in queries:
            refs.extend(self._refs_for_query(query, max_results, seen))
        refs.sort(key=lambda ref: ref.received_at, reverse=True)
        return refs[:max_results]

    def _fetch_full(self, message_id: str) -> RawEmail | None:
        """Fetch and parse the full message; None only if Gmail says it doesn't exist (404).

        Does not apply the allow-list and lets MessageParseError propagate, so sync can
        count missing, unparseable and not-allowed messages separately.
        """
        message = self._client.get_message(message_id, fmt="full")
        return None if message is None else parse_message(message)

    def _senders_to_search(self, sender: str | None) -> list[str]:
        """Return [sender] if it is allow-listed, or the whole allow-list when sender is None."""
        if sender is None:
            return sorted(self._allowed)
        if not self.is_allowed(sender):
            raise ValueError("sender is not on the newsletter allow-list")
        return [sender.lower()]

    def _refs_for_query(self, query: str, limit: int, seen: set[str]) -> list[NewsletterRef]:
        """Build refs for up to `limit` ids not seen in earlier queries.

        Ids are pulled lazily, so Gmail is paged and called at most `limit` times per query.
        """
        unseen = (
            message_id
            for message_id in self._client.get_message_ids(query)
            if message_id not in seen
        )
        refs: list[NewsletterRef] = []
        for message_id in islice(unseen, limit):
            seen.add(message_id)
            ref = self._fetch_ref(message_id)
            if ref is not None:
                refs.append(ref)
        return refs

    def _fetch_ref(self, message_id: str) -> NewsletterRef | None:
        """Fetch headers only and build a ref; None if missing, unparseable or not allowed."""
        message = self._client.get_message(
            message_id, fmt="metadata", metadata_headers=_REF_HEADERS
        )
        if message is None:
            _log_skip(message_id, "not found")
            return None
        try:
            email = parse_message(message)
        except MessageParseError as exc:
            _log_skip(message_id, str(exc))
            return None
        if not self.is_allowed(email.sender_email):
            _log_skip(message_id, "sender not allow-listed")
            return None
        return NewsletterRef(
            message_id=email.message_id,
            sender_email=email.sender_email,
            subject=email.subject,
            received_at=email.received_at,
        )


def _log_skip(message_id: str, reason: str) -> None:
    """Log a skipped message by id and a content-free reason.

    Deliberately logger.warning without exc_info: loguru's diagnose mode would print the
    traceback's local variables, which include the whole message dict.
    """
    logger.warning("Skipping message {}: {}", message_id, reason)
