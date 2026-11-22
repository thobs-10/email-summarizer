"""NewsletterGmailService: the only component that knows how Gmail works. Synchronous by design.

The ingestion pipeline calls it directly; the MCP server is a thin adapter over it. Every
result is re-checked against the sender allow-list after parsing, because Gmail's "from:"
search also matches display names. Logs carry message ids and content-free reasons only.

Thread safety: the Google API client is not thread-safe, and the MCP tools call this
service from worker threads (anyio.to_thread). Each public method that touches the client
holds one per-service lock for its whole run, so calls are serialised, including the
lazily paged id listing in find(). The lock is not re-entrant: public methods must not
call each other.
"""

import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import Any, Literal, Protocol

from loguru import logger

from email_summarizer.config.settings import GmailSettings
from email_summarizer.gmail.client import HistoryIdExpiredError
from email_summarizer.gmail.parser import MessageParseError, get_senders_address, parse_message
from email_summarizer.gmail.queries import build_sender_queries
from email_summarizer.ingestion.sinks import EmailSink
from email_summarizer.models.gmail_views import NewsletterRef, SyncResult
from email_summarizer.models.raw_email import MESSAGE_ID_PATTERN, RawEmail
from email_summarizer.state.gmail_state import SyncState

# find() only lists metadata, so it asks Gmail for these headers and no bodies.
_REF_HEADERS = ["From", "Subject"]

# sync's history path screens with this header before downloading any body (see sync).
_SCREEN_HEADERS = ["From"]

# The fallback query starts this long before the last sync, to cover clock skew.
_FALLBACK_OVERLAP = timedelta(days=1)

SyncMode = Literal["history", "query"]

# SyncResult count fields, used as the skip buckets below.
SkipBucket = Literal["skipped_not_allowed", "skipped_missing", "parse_errors"]
_WRITTEN = "written"
_NOT_ALLOWED: SkipBucket = "skipped_not_allowed"
_MISSING: SkipBucket = "skipped_missing"
_PARSE_ERROR: SkipBucket = "parse_errors"


class EmptyAllowListError(ValueError):
    """sync() was called with no allow-listed senders configured."""


class _Skipped(Exception):
    """A sync candidate that won't be written: `bucket` is its SyncResult count field.

    The message is a content-free reason (never subjects, bodies or addresses).
    """

    def __init__(self, bucket: SkipBucket, reason: str) -> None:
        super().__init__(reason)
        self.bucket = bucket


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
    """Read allow-listed newsletters from Gmail: check senders, fetch, find and sync them."""

    def __init__(
        self,
        client: GmailReader,
        settings: GmailSettings,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._allowed = frozenset(settings.newsletter_senders)  # already lower-cased
        self._days_back = settings.days_back
        self._now = now
        self._lock = threading.Lock()  # serialises every client call; see module docstring

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
        # fetch() takes ids from callers such as an LLM tool call; reject malformed ones
        # before Gmail sees them (it would answer 400 anyway).
        if not MESSAGE_ID_PATTERN.fullmatch(message_id):
            _log_skip("<invalid>", "malformed message id")  # never echo the caller's value
            return None
        try:
            with self._lock:
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
        with self._lock:
            for query in queries:
                refs.extend(self._refs_for_query(query, max_results, seen))
        refs.sort(key=lambda ref: ref.received_at, reverse=True)
        return refs[:max_results]

    def sync(self, state: SyncState, sink: EmailSink) -> SyncResult:
        """Write every allow-listed newsletter added since `state` to the sink.

        Uses the History API when `state` has a history ID, and an allow-list `after:` query
        on the first run or when that ID has expired. The new history ID is read before
        listing, so mail arriving mid-run is picked up next time (sink writes are idempotent,
        so the overlap is harmless). Missing, unparseable and not-allowed messages are
        skipped and counted.

        Args:
            state: Where the last successful sync left off; SyncState() before the first.
            sink: Destination for each email; must be idempotent by message_id.

        Raises:
            EmptyAllowListError: If the allow-list is empty; raised before any Gmail call, so an empty
                config can never advance the state past mail it would have filtered out.
            Exception: Whatever the sink or Gmail raises (e.g. HttpError). No result is
                returned then, so the caller keeps its old state and the next run retries.
                A write-phase abort is logged once ("Gmail sync aborted", counts and error
                type only). Callers must not re-log it with logger.exception while loguru's
                diagnose=True is on: the traceback locals would include email content.

        Returns:
            The counts and the new state, which the caller persists.
        """
        if not self._allowed:
            raise EmptyAllowListError("newsletter allow-list is empty")
        started, started_at = time.perf_counter(), self._now()
        counts: Counter[str] = Counter()
        with self._lock:
            new_history_id = self._client.get_current_history_id()
            mode, history_fallback, message_ids = self._sync_candidates(state)
            try:
                self._write_candidates(message_ids, sink, counts, screen=mode == "history")
            except Exception as exc:
                duration_ms = round((time.perf_counter() - started) * 1000)
                _log_sync_aborted(mode, len(message_ids), counts, exc, duration_ms)
                raise
        new_state = SyncState(history_id=new_history_id, last_synced=started_at)
        result = _sync_result(mode, history_fallback, len(message_ids), counts, new_state)
        _log_sync_summary(result, duration_ms=round((time.perf_counter() - started) * 1000))
        return result

    def _fetch_full(self, message_id: str) -> RawEmail | None:
        """Fetch and parse the full message; None only if Gmail says it doesn't exist (404).

        Does not apply the allow-list and lets MessageParseError propagate, so fetch() can
        log missing, unparseable and not-allowed messages with distinct reasons.
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

    def _sync_candidates(self, state: SyncState) -> tuple[SyncMode, bool, list[str]]:
        """Return (mode, history_fallback, unique candidate ids) for this run."""
        if state.history_id is None:
            return "query", False, self._query_candidates(state)
        try:
            message_ids, _ = self._client.list_added_message_ids(state.history_id)
        except HistoryIdExpiredError:
            logger.info("Gmail history ID expired; falling back to an after: query")
            return "query", True, self._query_candidates(state)
        return "history", False, _unique(message_ids)

    def _query_candidates(self, state: SyncState) -> list[str]:
        """Ids from allow-list queries since the last sync (minus overlap), else days_back."""
        if state.last_synced is not None:
            # A last_synced in the future (clock skew, hand-edited state) must not hide mail.
            after = min(state.last_synced, self._now()) - _FALLBACK_OVERLAP
        else:
            after = self._now() - timedelta(days=self._days_back)
        queries = build_sender_queries(sorted(self._allowed), after=after)
        return _unique(mid for query in queries for mid in self._client.get_message_ids(query))

    def _write_candidates(
        self, message_ids: list[str], sink: EmailSink, counts: Counter[str], *, screen: bool
    ) -> None:
        """Load and write each candidate in order; count each into exactly one bucket.

        Counts go into the caller's `counts`, so they survive an abort for the log. Stops
        at the first sink (or Gmail) error by letting it propagate.
        """
        for message_id in message_ids:
            try:
                email = self._load_for_sync(message_id, screen=screen)
            except _Skipped as skip:
                quiet = screen and skip.bucket == _NOT_ALLOWED
                _log_skip(message_id, str(skip), level="DEBUG" if quiet else "WARNING")
                counts[skip.bucket] += 1
                continue
            sink.write(email)
            counts[_WRITTEN] += 1

    def _load_for_sync(self, message_id: str, *, screen: bool) -> RawEmail:
        """Return the full, allow-listed email or raise _Skipped.

        With screen=True (history path, which lists all new mail, not just newsletters),
        the From header is checked first so bodies of other mail are never downloaded.
        The full message is still re-checked after parsing, since that is what is written.
        """
        if screen:
            self._screen_sender(message_id)
        return self._load_allowed(message_id)

    def _screen_sender(self, message_id: str) -> None:
        """Raise _Skipped unless the From header alone names an allow-listed address.

        Only the address is read, not the whole message: a message from an allow-listed
        sender that is broken elsewhere must reach the full parse and count as a parse
        error, so a parser regression can't hide among other people's mail.
        """
        message = self._client.get_message(
            message_id, fmt="metadata", metadata_headers=_SCREEN_HEADERS
        )
        if message is None:
            raise _Skipped(_MISSING, "not found")
        address = get_senders_address(message)
        if address is None or not self.is_allowed(address):
            raise _Skipped(_NOT_ALLOWED, "sender not allow-listed")

    def _load_allowed(self, message_id: str) -> RawEmail:
        """Fetch and parse the full message, then apply the allow-list; else raise _Skipped."""
        message = self._client.get_message(message_id, fmt="full")
        if message is None:
            raise _Skipped(_MISSING, "not found")
        try:
            email = parse_message(message)
        except MessageParseError as exc:
            raise _Skipped(_PARSE_ERROR, str(exc)) from exc
        if not self.is_allowed(email.sender_email):
            raise _Skipped(_NOT_ALLOWED, "sender not allow-listed")
        return email


def _unique(message_ids: Iterable[str]) -> list[str]:
    """Drop repeated ids, keeping first-seen order (chunked queries can overlap)."""
    return list(dict.fromkeys(message_ids))


def _sync_result(
    mode: SyncMode,
    history_fallback: bool,
    candidates: int,
    counts: Counter[str],
    new_state: SyncState,
) -> SyncResult:
    """Build the SyncResult from the per-bucket counts."""
    return SyncResult(
        mode=mode,
        history_fallback=history_fallback,
        candidates=candidates,
        written=counts[_WRITTEN],
        skipped_not_allowed=counts[_NOT_ALLOWED],
        skipped_missing=counts[_MISSING],
        parse_errors=counts[_PARSE_ERROR],
        new_state=new_state,
    )


def _log_sync_aborted(
    mode: SyncMode, candidates: int, counts: Counter[str], exc: Exception, duration_ms: int
) -> None:
    """Log one counts-only line when the write phase fails; the caller re-raises.

    Only the exception type is logged: its message (sink or HttpError) may carry content.
    """
    fields: dict[str, object] = {
        "mode": mode,
        "candidates": candidates,
        "written": counts[_WRITTEN],
        "filtered_out": counts[_NOT_ALLOWED],
        "errors": counts[_MISSING] + counts[_PARSE_ERROR],
        "error_type": type(exc).__name__,
        "duration_ms": duration_ms,
    }
    summary = " ".join(f"{name}={value}" for name, value in fields.items())
    logger.bind(**fields).warning("Gmail sync aborted: {}", summary)


def _log_sync_summary(result: SyncResult, duration_ms: int) -> None:
    """Log one counts-only summary per sync; warn if every candidate failed to load."""
    errors = result.skipped_missing + result.parse_errors
    fields: dict[str, object] = {
        "mode": result.mode,
        "candidates": result.candidates,
        "filtered_out": result.skipped_not_allowed,
        "written": result.written,
        "errors": errors,
        "skipped_missing": result.skipped_missing,
        "parse_errors": result.parse_errors,
        "duration_ms": duration_ms,
        "history_fallback": result.history_fallback,
    }
    summary = " ".join(f"{name}={value}" for name, value in fields.items())
    logger.bind(**fields).info("Gmail sync finished: {}", summary)
    # Filtered-out mail (other senders) is not a failure; compare against the rest.
    if errors and errors == result.candidates - result.skipped_not_allowed:
        logger.warning("Gmail sync: every candidate failed ({} of {})", errors, errors)


def _log_skip(message_id: str, reason: str, level: str = "WARNING") -> None:
    """Log a skipped message by id and a content-free reason.

    Deliberately logger.warning/log without exc_info: loguru's diagnose mode would print the
    traceback's local variables, which include the whole message dict.
    """
    logger.log(level, "Skipping message {}: {}", message_id, reason)
