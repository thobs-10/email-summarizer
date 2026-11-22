"""Tests for NewsletterGmailService.is_allowed, fetch, find and sync.

FakeGmailClient, FakeSink, the service fixtures and make_newsletter live in
tests/gmail/conftest.py.
"""

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from email_summarizer.config.settings import GmailSettings
from email_summarizer.gmail.service import EmptyAllowListError, NewsletterGmailService
from email_summarizer.ingestion.sinks import JsonFileSink
from email_summarizer.models.gmail_views import NewsletterRef
from email_summarizer.models.raw_email import RawEmail
from email_summarizer.state.gmail_state import SyncState

# ----------------------------------
# is_allowed
# ----------------------------------


@pytest.mark.parametrize("address", ["news@example.com", "NEWS@Example.COM"])
def test_is_allowed_matches_case_insensitively(newsletter_service, address):
    assert newsletter_service.is_allowed(address)


@pytest.mark.parametrize(
    "address", ["evil@example.com", "news@example.com.evil.org", "xnews@example.com", ""]
)
def test_is_allowed_rejects_other_addresses(newsletter_service, address):
    assert not newsletter_service.is_allowed(address)


# ----------------------------------
# fetch
# ----------------------------------


def test_fetch_returns_full_allow_listed_message(newsletter_service, fake_client, make_newsletter):
    fake_client.add(make_newsletter("m1"))

    email = newsletter_service.fetch("m1")

    assert email is not None
    assert (email.message_id, email.sender_email) == ("m1", "news@example.com")
    assert email.text_body == "Secret body text"
    assert fake_client.get_calls == [("m1", "full")]


def test_fetch_returns_none_for_missing_message(newsletter_service, log_messages):
    assert newsletter_service.fetch("gone") is None
    assert any("gone" in line and "not found" in line for line in log_messages)


def test_fetch_returns_none_for_disallowed_sender(
    newsletter_service, fake_client, make_newsletter, log_messages
):
    fake_client.add(make_newsletter("m1", "Bank <alerts@bank.com>", subject="Your statement"))

    assert newsletter_service.fetch("m1") is None
    assert any("m1" in line and "not allow-listed" in line for line in log_messages)
    assert not any("bank" in line.lower() or "statement" in line for line in log_messages)


def test_fetch_returns_none_for_unparseable_message_and_logs_id_only(
    newsletter_service, fake_client, make_newsletter, log_messages
):
    message = make_newsletter("m1", subject="Private subject")
    message["internalDate"] = "not-a-number"
    fake_client.add(message)

    assert newsletter_service.fetch("m1") is None
    assert any("m1" in line and "internalDate" in line for line in log_messages)
    joined = "\n".join(log_messages)
    for content in ("Private subject", "news@example.com", "Secret body text"):
        assert content not in joined


@pytest.mark.parametrize(
    "message_id", ["../x", "hello world", "m1\nforged log line", "a" * 65, "", "m1;drop"]
)
def test_fetch_rejects_malformed_id_before_calling_gmail(
    newsletter_service, fake_client, make_newsletter, log_messages, message_id
):
    fake_client.add(make_newsletter("m1"))

    assert newsletter_service.fetch(message_id) is None
    assert fake_client.get_calls == []
    assert any("<invalid>" in line and "malformed message id" in line for line in log_messages)
    if message_id:
        assert not any(message_id in line for line in log_messages)


def test_fetch_propagates_gmail_errors_other_than_404(newsletter_service, fake_client):
    fake_client.errors["m1"] = HttpError(Response({"status": 500}), b"error")

    with pytest.raises(HttpError):
        newsletter_service.fetch("m1")


def test_fetch_full_returns_none_only_for_missing(newsletter_service, fake_client, make_newsletter):
    """The private helper fetch uses: no allow-list check, parse errors propagate."""
    from email_summarizer.gmail.parser import MessageParseError

    bad = make_newsletter("bad")
    del bad["threadId"]
    fake_client.add(make_newsletter("other", "x@elsewhere.com"), bad)

    assert newsletter_service._fetch_full("gone") is None
    assert newsletter_service._fetch_full("other").sender_email == "x@elsewhere.com"
    with pytest.raises(MessageParseError):
        newsletter_service._fetch_full("bad")


# ----------------------------------
# find
# ----------------------------------


def test_find_returns_refs_newest_first_and_capped(
    newsletter_service, fake_client, make_newsletter
):
    for day in (3, 9, 5, 7):
        fake_client.add(make_newsletter(f"m{day}", received_at=datetime(2026, 10, day, tzinfo=UTC)))

    refs = newsletter_service.find(None, days=30, max_results=2)

    assert [ref.message_id for ref in refs] == ["m9", "m7"]
    assert refs[0] == NewsletterRef(
        message_id="m9",
        sender_email="news@example.com",
        subject="Weekly digest",
        received_at=datetime(2026, 10, 9, tzinfo=UTC),
    )


def test_find_fetches_metadata_only_and_lazily(newsletter_service, fake_client, make_newsletter):
    fake_client.add(*(make_newsletter(f"m{i}") for i in range(5)))

    newsletter_service.find(None, days=7, max_results=2)

    assert fake_client.get_calls == [("m0", "metadata"), ("m1", "metadata")]


def test_find_without_sender_queries_whole_allow_list(newsletter_service, fake_client):
    newsletter_service.find(None, days=7, max_results=5)

    assert len(fake_client.queries) == 1
    assert "digest@example.org" in fake_client.queries[0]
    assert "news@example.com" in fake_client.queries[0]


def test_find_with_sender_uses_only_that_sender_and_after_epoch(
    newsletter_service, fake_client, fixed_now
):
    newsletter_service.find("NEWS@example.com", days=3, max_results=5)

    epoch = int((fixed_now - timedelta(days=3)).timestamp())
    assert fake_client.queries == [f"from:(news@example.com) after:{epoch}"]


def test_find_with_disallowed_sender_raises_before_calling_gmail(newsletter_service, fake_client):
    with pytest.raises(ValueError, match="not on the newsletter allow-list") as exc_info:
        newsletter_service.find("evil@example.com", days=7, max_results=5)

    assert "evil" not in str(exc_info.value)
    assert fake_client.queries == []


def test_find_skips_spoofed_sender_missing_and_unparseable(
    newsletter_service, fake_client, make_newsletter, log_messages
):
    spoofed = make_newsletter("spoofed", '"news@example.com" <evil@x.com>')
    unparseable = make_newsletter("bad")
    unparseable["internalDate"] = "soon"
    fake_client.add(make_newsletter("good"), spoofed, unparseable)
    fake_client.ids_for_query = lambda _query: ["good", "spoofed", "gone", "bad"]

    refs = newsletter_service.find(None, days=7, max_results=10)

    assert [ref.message_id for ref in refs] == ["good"]
    joined = "\n".join(log_messages)
    for message_id in ("spoofed", "gone", "bad"):
        assert f"Skipping message {message_id}:" in joined
    assert "evil@x.com" not in joined


def test_find_dedupes_ids_across_chunked_queries(make_service, fake_client, make_newsletter):
    senders = [f"sender{i:02d}@newsletters-example.com" for i in range(40)]
    service = make_service(senders)
    fake_client.add(make_newsletter("shared", senders[0]))
    fake_client.ids_for_query = lambda _query: ["shared"]

    refs = service.find(None, days=7, max_results=10)

    assert len(fake_client.queries) > 1  # the allow-list was split into several queries
    assert [ref.message_id for ref in refs] == ["shared"]
    assert fake_client.get_calls == [("shared", "metadata")]


def test_find_merges_chunks_newest_first_and_truncates_globally(
    make_service, fake_client, make_newsletter
):
    senders = [f"sender{i:02d}@newsletters-example.com" for i in range(40)]
    service = make_service(senders)
    days = {"old1": 1, "old2": 2, "new1": 8, "new2": 9}
    fake_client.add(
        *(
            make_newsletter(i, senders[0], received_at=datetime(2026, 10, d, tzinfo=UTC))
            for i, d in days.items()
        )
    )
    # The first chunk only finds older messages; a later chunk finds newer ones.
    fake_client.ids_for_query = lambda query: (
        ["old2", "old1"] if senders[0] in query else ["new2", "new1"]
    )

    refs = service.find(None, days=30, max_results=2)

    assert len(fake_client.queries) > 1
    assert [ref.message_id for ref in refs] == ["new2", "new1"]


def test_find_propagates_gmail_errors_other_than_404(
    newsletter_service, fake_client, make_newsletter
):
    fake_client.add(make_newsletter("m1"))
    fake_client.errors["m1"] = HttpError(Response({"status": 500}), b"error")

    with pytest.raises(HttpError):
        newsletter_service.find(None, days=7, max_results=5)


@pytest.mark.parametrize(("days", "max_results"), [(0, 5), (7, 0), (-1, 5), (7, -3)])
def test_find_rejects_non_positive_limits(newsletter_service, fake_client, days, max_results):
    with pytest.raises(ValueError, match="at least 1"):
        newsletter_service.find(None, days=days, max_results=max_results)

    assert fake_client.queries == []


def test_find_with_empty_allow_list_raises_before_calling_gmail(make_service, fake_client):
    service = make_service([])

    with pytest.raises(ValueError, match="at least one sender"):
        service.find(None, days=7, max_results=5)

    assert fake_client.queries == []
    assert fake_client.get_calls == []


def test_default_clock_is_aware_utc(fake_client, gmail_settings):
    from email_summarizer.gmail.service import NewsletterGmailService

    now = NewsletterGmailService(fake_client, gmail_settings)._now()

    assert now.tzinfo is UTC


# ----------------------------------
# sync
# ----------------------------------

PERSONAL_FROM = "Bank <alerts@bank.com>"


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


def test_sync_history_path_writes_allowed_and_advances_state(
    newsletter_service, fake_client, fake_sink, make_newsletter, fixed_now
):
    fake_client.add(make_newsletter("m1"), make_newsletter("p1", PERSONAL_FROM))
    fake_client.history_added["100"] = ["m1", "p1"]

    result = newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert (result.mode, result.history_fallback) == ("history", False)
    assert (result.candidates, result.written, result.skipped_not_allowed) == (2, 1, 1)
    assert list(fake_sink.emails) == ["m1"]
    assert result.new_state == SyncState(history_id="500", last_synced=fixed_now)
    assert fake_client.history_calls == ["100"]
    assert fake_client.queries == []


def test_sync_history_path_never_downloads_bodies_of_other_mail(
    newsletter_service, fake_client, fake_sink, make_newsletter
):
    """The History API returns all new mail, so non-newsletters are screened by headers."""
    fake_client.add(make_newsletter("m1"), make_newsletter("p1", PERSONAL_FROM))
    fake_client.history_added["100"] = ["m1", "p1"]

    newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert ("p1", "full") not in fake_client.get_calls
    assert fake_client.get_calls == [("m1", "metadata"), ("m1", "full"), ("p1", "metadata")]


@pytest.mark.parametrize(
    "spoofed_from", ['"Substack" <evil@x.com>', '"news@example.com" <evil@x.com>']
)
@pytest.mark.parametrize("state", [SyncState(history_id="100"), SyncState()])
def test_sync_filters_spoofed_display_names_on_both_paths(
    newsletter_service, fake_client, fake_sink, make_newsletter, log_messages, spoofed_from, state
):
    fake_client.add(make_newsletter("m1"), make_newsletter("spoof", spoofed_from))
    fake_client.history_added["100"] = ["m1", "spoof"]

    result = newsletter_service.sync(state, fake_sink)

    assert list(fake_sink.emails) == ["m1"]
    assert result.skipped_not_allowed == 1
    assert "evil@x.com" not in "\n".join(log_messages)


def test_sync_falls_back_to_query_when_history_id_expired(
    newsletter_service, fake_client, fake_sink, make_newsletter, fixed_now, log_messages
):
    last_synced = fixed_now - timedelta(days=3)
    fake_client.add(make_newsletter("m1"))
    fake_client.expired_history_ids.add("100")

    result = newsletter_service.sync(
        SyncState(history_id="100", last_synced=last_synced), fake_sink
    )

    assert (result.mode, result.history_fallback, result.written) == ("query", True, 1)
    assert result.new_state.history_id == "500"
    # One day of overlap before the last sync covers clock skew.
    after = _epoch(last_synced - timedelta(days=1))
    assert fake_client.queries == [f"from:(digest@example.org OR news@example.com) after:{after}"]
    assert any("history_fallback=True" in line for line in log_messages)


def test_sync_first_run_queries_last_days_back_days(
    newsletter_service, fake_client, fake_sink, make_newsletter, fixed_now
):
    fake_client.add(make_newsletter("m1"), make_newsletter("m2"))

    result = newsletter_service.sync(SyncState(), fake_sink)

    assert (result.mode, result.history_fallback, result.written) == ("query", False, 2)
    assert fake_client.history_calls == []
    assert fake_client.queries[0].endswith(f"after:{_epoch(fixed_now - timedelta(days=7))}")
    assert result.new_state == SyncState(history_id="500", last_synced=fixed_now)


def test_sync_query_path_fetches_full_messages_only(
    newsletter_service, fake_client, fake_sink, make_newsletter
):
    """Query results already match from:, so no metadata pre-screen call is spent."""
    fake_client.add(make_newsletter("m1"))

    newsletter_service.sync(SyncState(), fake_sink)

    assert fake_client.get_calls == [("m1", "full")]


def test_sync_query_path_counts_messages_deleted_before_fetch(
    newsletter_service, fake_client, fake_sink, make_newsletter
):
    fake_client.add(make_newsletter("m1"))
    fake_client.ids_for_query = lambda _query: ["m1", "gone"]

    result = newsletter_service.sync(SyncState(), fake_sink)

    assert (result.candidates, result.written, result.skipped_missing) == (2, 1, 1)


def test_sync_records_history_id_before_listing(
    newsletter_service, fake_client, fake_sink, make_newsletter
):
    """Mail arriving mid-run is picked up next time instead of being skipped forever."""
    fake_client.add(make_newsletter("m1"))

    def list_and_receive_new_mail(_query: str) -> list[str]:
        fake_client.history_id = "999"
        return ["m1"]

    fake_client.ids_for_query = list_and_receive_new_mail

    result = newsletter_service.sync(SyncState(), fake_sink)

    assert result.new_state.history_id == "500"


def test_sync_does_not_return_new_state_when_sink_raises(
    newsletter_service, fake_client, fake_sink, make_newsletter
):
    fake_client.add(make_newsletter("m1"), make_newsletter("m2"), make_newsletter("m3"))
    fake_client.history_added["100"] = ["m1", "m2", "m3"]
    fake_sink.fail_on.add("m2")

    with pytest.raises(OSError, match="sink unavailable"):
        newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert fake_sink.writes == ["m1", "m2"]  # stops at the failure; m3 is not attempted

    # The caller keeps the old state, so a retry re-lists every id and loses nothing.
    fake_sink.fail_on.clear()
    result = newsletter_service.sync(SyncState(history_id="100"), fake_sink)
    assert sorted(fake_sink.emails) == ["m1", "m2", "m3"]
    assert result.written == 3


def test_sync_propagates_gmail_errors_other_than_404(
    newsletter_service, fake_client, fake_sink, make_newsletter
):
    fake_client.add(make_newsletter("m1"))
    fake_client.errors["m1"] = HttpError(Response({"status": 500}), b"error")

    with pytest.raises(HttpError):
        newsletter_service.sync(SyncState(), fake_sink)

    assert fake_sink.writes == []


def test_sync_rerun_is_idempotent(newsletter_service, fake_client, fake_sink, make_newsletter):
    fake_client.add(make_newsletter("m1"), make_newsletter("m2"))
    first = newsletter_service.sync(SyncState(), fake_sink)
    stored = dict(fake_sink.emails)

    second = newsletter_service.sync(first.new_state, fake_sink)  # nothing new since "500"

    assert (second.mode, second.candidates, second.written) == ("history", 0, 0)
    assert fake_sink.emails == stored

    # An overlapping fallback re-run rewrites the same ids and changes nothing.
    newsletter_service.sync(SyncState(last_synced=first.new_state.last_synced), fake_sink)
    assert fake_sink.emails == stored


def test_sync_writes_through_json_file_sink(
    newsletter_service, fake_client, make_newsletter, tmp_path
):
    fake_client.add(make_newsletter("m1"), make_newsletter("m2"))
    sink = JsonFileSink(tmp_path / "raw")

    result = newsletter_service.sync(SyncState(), sink)

    assert result.written == 2
    assert sorted(p.name for p in sink.directory.iterdir()) == ["m1.json", "m2.json"]
    stored = RawEmail.model_validate_json((sink.directory / "m1.json").read_text())
    assert stored.message_id == "m1"


def test_sync_dedupes_ids_within_a_run(make_service, fake_client, fake_sink, make_newsletter):
    senders = [f"sender{i:02d}@newsletters-example.com" for i in range(40)]
    service = make_service(senders)
    fake_client.add(make_newsletter("shared", senders[0]))
    fake_client.ids_for_query = lambda _query: ["shared", "shared"]

    result = service.sync(SyncState(), fake_sink)

    assert len(fake_client.queries) > 1  # the allow-list was split into several queries
    assert (result.candidates, result.written) == (1, 1)
    assert fake_sink.writes == ["shared"]


def test_sync_skips_and_counts_by_reason_without_pii(
    newsletter_service, fake_client, fake_sink, make_newsletter, make_bad_body, log_records
):
    bad_header = make_newsletter("badhead")
    bad_header["internalDate"] = "soon"  # allow-listed From: passes the screen, fails parsing
    fake_client.add(make_newsletter("m1"), make_newsletter("p1", PERSONAL_FROM), bad_header)
    fake_client.add(make_bad_body("badbody"))
    fake_client.history_added["100"] = ["m1", "p1", "gone", "badbody", "badhead"]

    result = newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert (result.candidates, result.written) == (5, 1)
    assert (result.skipped_not_allowed, result.skipped_missing, result.parse_errors) == (1, 1, 2)
    skip_levels = {
        msg.split(":")[0].removeprefix("Skipping message "): level
        for level, msg in log_records
        if msg.startswith("Skipping message")
    }
    assert skip_levels == {
        "p1": "DEBUG",
        "gone": "WARNING",
        "badbody": "WARNING",
        "badhead": "WARNING",
    }
    joined = "\n".join(msg for _, msg in log_records)
    for content in ("Private subject", "alerts@bank.com", "Secret body text", "Traceback"):
        assert content not in joined


def test_sync_logs_one_structured_summary(
    newsletter_service, fake_client, fake_sink, make_newsletter, log_messages
):
    fake_client.add(make_newsletter("m1"), make_newsletter("p1", PERSONAL_FROM))
    fake_client.history_added["100"] = ["m1", "p1", "gone"]

    newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    summaries = [line for line in log_messages if line.startswith("Gmail sync finished")]
    assert len(summaries) == 1
    for field in (
        "mode=history",
        "candidates=3",
        "filtered_out=1",
        "written=1",
        "errors=1",
        "duration_ms=",
        "history_fallback=False",
    ):
        assert field in summaries[0]


@pytest.mark.parametrize("state", [SyncState(history_id="100"), SyncState()])
def test_sync_with_empty_allow_list_fails_before_calling_gmail(
    make_service, fake_client, fake_sink, state
):
    """Otherwise the history path would advance state past mail it silently filtered out."""
    service = make_service([])

    with pytest.raises(EmptyAllowListError, match="newsletter allow-list is empty"):
        service.sync(state, fake_sink)

    assert (fake_client.history_calls, fake_client.queries, fake_client.get_calls) == ([], [], [])
    assert fake_sink.writes == []


def test_sync_clamps_future_last_synced_to_now(
    newsletter_service, fake_client, fake_sink, fixed_now
):
    newsletter_service.sync(SyncState(last_synced=fixed_now + timedelta(days=30)), fake_sink)

    assert fake_client.queries[0].endswith(f"after:{_epoch(fixed_now - timedelta(days=1))}")


def test_sync_screen_counts_unparseable_from_as_not_allowed_at_debug(
    newsletter_service, fake_client, fake_sink, make_newsletter, log_records
):
    fake_client.add(make_newsletter("noaddr", "Undisclosed sender"))
    fake_client.history_added["100"] = ["noaddr"]

    result = newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert (result.skipped_not_allowed, result.parse_errors) == (1, 0)
    assert ("noaddr", "full") not in fake_client.get_calls
    skips = [(level, msg) for level, msg in log_records if msg.startswith("Skipping message")]
    assert [level for level, _ in skips] == ["DEBUG"]
    assert not any("every candidate failed" in msg for _, msg in log_records)


@pytest.mark.parametrize("failure", ["sink", "gmail"])
def test_sync_logs_one_aborted_line_with_error_type_only(
    newsletter_service, fake_client, fake_sink, make_newsletter, log_records, failure
):
    fake_client.add(make_newsletter("m1"), make_newsletter("m2", subject="Private subject"))
    fake_client.history_added["100"] = ["m1", "m2"]
    if failure == "sink":
        fake_sink.fail_on.add("m2")
        expected = OSError
    else:
        fake_client.errors["m2"] = HttpError(Response({"status": 500}), b"secret body")
        expected = HttpError

    with pytest.raises(expected):
        newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    aborted = [(lvl, msg) for lvl, msg in log_records if msg.startswith("Gmail sync aborted")]
    assert len(aborted) == 1
    level, message = aborted[0]
    assert level == "WARNING"
    for field in (
        "mode=history",
        "candidates=2",
        "written=1",
        f"error_type={expected.__name__}",
        "duration_ms=",
    ):
        assert field in message
    joined = "\n".join(msg for _, msg in log_records)
    for content in ("sink unavailable", "secret body", "Private subject", "Traceback"):
        assert content not in joined
    assert not any(msg.startswith("Gmail sync finished") for _, msg in log_records)


def test_sync_counts_allow_listed_parse_failures_as_errors_on_history_path(
    newsletter_service, fake_client, fake_sink, make_newsletter, log_messages
):
    """A parser regression must show up as errors, not hide as filtered-out mail."""
    for message_id in ("b1", "b2", "b3"):
        bad = make_newsletter(message_id)
        bad["internalDate"] = "soon"
        fake_client.add(bad)
    fake_client.history_added["100"] = ["b1", "b2", "b3"]

    result = newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert (result.parse_errors, result.skipped_not_allowed) == (3, 0)
    assert any("every candidate failed (3 of 3)" in line for line in log_messages)


def test_sync_warns_when_every_newsletter_failed_in_a_mixed_inbox(
    newsletter_service, fake_client, fake_sink, make_newsletter, make_bad_body, log_messages
):
    """Personal mail on the history path is filtered out, not a reason to stay quiet."""
    fake_client.add(make_bad_body("bad"), make_newsletter("p1", PERSONAL_FROM))
    fake_client.history_added["100"] = ["bad", "p1"]

    result = newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert (result.parse_errors, result.skipped_not_allowed, result.written) == (1, 1, 0)
    assert any("every candidate failed (1 of 1)" in line for line in log_messages)


def test_sync_warns_when_every_candidate_failed(
    newsletter_service, fake_client, fake_sink, make_bad_body, log_messages
):
    fake_client.add(make_bad_body("bad"))
    fake_client.history_added["100"] = ["bad", "gone"]

    result = newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert (result.written, result.parse_errors, result.skipped_missing) == (0, 1, 1)
    assert any("every candidate failed (2 of 2)" in line for line in log_messages)


@pytest.mark.parametrize("from_header", [None, PERSONAL_FROM])
def test_sync_does_not_warn_when_nothing_failed(
    newsletter_service, fake_client, fake_sink, make_newsletter, log_messages, from_header
):
    """No candidates, or only other people's mail, is a normal quiet run."""
    if from_header:
        fake_client.add(make_newsletter("p1", from_header))
        fake_client.history_added["100"] = ["p1"]

    newsletter_service.sync(SyncState(history_id="100"), fake_sink)

    assert not any("every candidate failed" in line for line in log_messages)


# ----------------------------------
# thread safety: MCP tools call the service from worker threads
# ----------------------------------
class OverlapDetectingClient:
    """Wraps FakeGmailClient, recording the most Gmail calls ever in flight at once.

    Each call sleeps briefly so unserialised callers would overlap. Paged id listing is
    tracked per page fetch too, since find() consumes that generator lazily.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._guard = threading.Lock()
        self._active = 0
        self.max_active = 0

    @contextmanager
    def _call(self) -> Iterator[None]:
        with self._guard:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        time.sleep(0.002)
        try:
            yield
        finally:
            with self._guard:
                self._active -= 1

    def get_message(
        self, message_id: str, fmt: str = "full", metadata_headers: list[str] | None = None
    ) -> dict[str, Any] | None:
        with self._call():
            return self._inner.get_message(message_id, fmt, metadata_headers)

    def get_message_ids(self, query: str) -> Iterator[str]:
        ids = iter(self._inner.get_message_ids(query))
        while True:
            with self._call():
                message_id = next(ids, None)
            if message_id is None:
                return
            yield message_id

    def get_current_history_id(self) -> str:
        with self._call():
            return self._inner.get_current_history_id()

    def list_added_message_ids(self, start_history_id: str) -> tuple[list[str], str]:
        with self._call():
            return self._inner.list_added_message_ids(start_history_id)


def test_service_serialises_gmail_calls_across_threads(
    fake_client, fake_sink, make_newsletter, fixed_now, tmp_path
):
    fake_client.add(*(make_newsletter(f"m{i}") for i in range(3)))
    fake_client.history_added["100"] = ["m0", "m1"]
    client = OverlapDetectingClient(fake_client)
    settings = GmailSettings(base_dir=tmp_path, newsletter_senders=["news@example.com"])
    service = NewsletterGmailService(client, settings, now=lambda: fixed_now)
    calls = [
        lambda: service.find(None, 7, 10),
        lambda: service.fetch("m1"),
        lambda: service.sync(SyncState(history_id="100"), fake_sink),
    ] * 4

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        results = [future.result() for future in [pool.submit(call) for call in calls]]

    assert client.max_active == 1
    assert len(results[0]) == 3 and results[1] is not None and results[2].written == 2
