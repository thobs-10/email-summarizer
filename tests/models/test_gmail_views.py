"""Unit tests for NewsletterRef and SyncResult."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from email_summarizer.models.gmail_views import NewsletterRef, SyncResult
from email_summarizer.state.gmail_state import SyncState

COUNT_FIELDS = ["candidates", "written", "skipped_not_allowed", "skipped_missing", "parse_errors"]


def make_result(**overrides: object) -> SyncResult:
    # Every bucket is non-zero and they sum to candidates, as the validator requires.
    fields = {
        "mode": "query",
        "history_fallback": False,
        "candidates": 4,
        "written": 1,
        "skipped_not_allowed": 1,
        "skipped_missing": 1,
        "parse_errors": 1,
        "new_state": SyncState(history_id="42"),
    }
    # model_validate takes a plain dict; **-unpacking one would lose the field types for mypy.
    return SyncResult.model_validate(fields | overrides)


def test_newsletter_ref_builds():
    received = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    ref = NewsletterRef(
        message_id="msg-1", sender_email="news@example.com", subject="Hi", received_at=received
    )
    assert ref.message_id == "msg-1" and ref.received_at == received


def test_sync_result_accepts_valid_values():
    result = make_result(mode="history", history_fallback=True)
    assert result.mode == "history" and result.history_fallback is True
    assert result.new_state.history_id == "42"


@pytest.mark.parametrize("field", COUNT_FIELDS)
def test_sync_result_rejects_negative_counts(field):
    with pytest.raises(ValidationError):
        make_result(**{field: -1})


@pytest.mark.parametrize("field", COUNT_FIELDS)
def test_sync_result_rejects_counts_that_do_not_add_up(field):
    result = make_result()
    with pytest.raises(ValidationError, match="bucket counts"):
        make_result(**{field: getattr(result, field) + 1})


def test_newsletter_ref_rejects_naive_received_at():
    with pytest.raises(ValidationError):
        NewsletterRef(
            message_id="m", sender_email="a@b.co", subject="s", received_at=datetime(2026, 1, 1)
        )


def test_sync_result_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        make_result(mode="full")


def test_sync_result_is_frozen():
    result = make_result()
    with pytest.raises(ValidationError):
        result.written = 99


def test_newsletter_ref_is_frozen():
    ref = NewsletterRef(
        message_id="m", sender_email="a@b.co", subject="s", received_at=datetime.now(UTC)
    )
    with pytest.raises(ValidationError):
        ref.subject = "changed"
