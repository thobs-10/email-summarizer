"""Tests for NewsletterGmailService.is_allowed, fetch and find.

FakeGmailClient, the service fixtures and make_newsletter live in tests/gmail/conftest.py.
"""

from datetime import UTC, datetime, timedelta

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from email_summarizer.models.gmail_views import NewsletterRef

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
    """The private helper sync will reuse: no allow-list check, parse errors propagate."""
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
