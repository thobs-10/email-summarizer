"""Unit tests for the RawEmail model."""

from datetime import UTC, datetime
from typing import get_args, get_type_hints

import pytest
from pydantic import ValidationError

from email_summarizer.models.raw_email import RawEmail

REQUIRED_FIELDS = [
    "message_id",
    "thread_id",
    "label_ids",
    "sender_id",
    "sender_email",
    "received_at",
    "subject",
]


# --------------------------------------------------------------------------------------
# Construction and validation
# --------------------------------------------------------------------------------------


def test_valid_email_keeps_all_fields(make_email):
    email = make_email()
    assert email.message_id == "msg-1"
    assert email.thread_id == "thread-1"
    assert email.label_ids == ["INBOX", "CATEGORY_UPDATES"]
    assert email.sender_id == "sender-1"
    assert email.sender_email == "news@example.com"
    assert email.received_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert email.subject == "Weekly digest"


def test_bodies_are_optional_and_default_to_none(make_email):
    email = make_email(html_body=None, text_body=None)
    assert email.html_body is None and email.text_body is None

    data = make_email().model_dump(exclude={"html_body", "text_body"})
    minimal = RawEmail(**data)  # fields omitted entirely, not just set to None
    assert minimal.html_body is None and minimal.text_body is None


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_required_fields_are_enforced(make_email, missing):
    data = make_email().model_dump()
    del data[missing]
    with pytest.raises(ValidationError) as exc:
        RawEmail(**data)
    assert missing in str(exc.value)


def test_received_at_is_parsed_from_iso_string(make_email):
    email = make_email(received_at="2026-01-01T12:00:00+00:00")
    assert email.received_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert email.received_at.tzinfo is not None


def test_invalid_received_at_is_rejected(make_email):
    with pytest.raises(ValidationError):
        make_email(received_at="not a date")


def test_empty_label_list_is_allowed(make_email):
    assert make_email(label_ids=[]).label_ids == []


@pytest.mark.parametrize("bad_labels", ["INBOX", [1, 2], None])
def test_label_ids_must_be_a_list_of_strings(make_email, bad_labels):
    with pytest.raises(ValidationError):
        make_email(label_ids=bad_labels)


@pytest.mark.parametrize("field", ["message_id", "subject", "sender_email"])
def test_string_fields_reject_non_strings(make_email, field):
    with pytest.raises(ValidationError):
        make_email(**{field: 123})


# --------------------------------------------------------------------------------------
# preferred_body
# --------------------------------------------------------------------------------------


def test_preferred_body_returns_html_when_available(make_email):
    email = make_email(html_body="<p>Hi</p>", text_body="Hi")
    assert email.preferred_body == ("<p>Hi</p>", "html")


def test_preferred_body_falls_back_to_text(make_email):
    email = make_email(html_body=None, text_body="Just text")
    assert email.preferred_body == ("Just text", "text")


def test_preferred_body_is_none_without_any_body(make_email):
    assert make_email(html_body=None, text_body=None).preferred_body is None


@pytest.mark.parametrize("empty_html", [None, ""])
def test_empty_html_counts_as_missing(make_email, empty_html):
    email = make_email(html_body=empty_html, text_body="Fallback")
    assert email.preferred_body == ("Fallback", "text")


def test_empty_strings_for_both_bodies_return_none(make_email):
    assert make_email(html_body="", text_body="").preferred_body is None


def test_preferred_body_format_label_matches_declared_literal(make_email):
    """Guards against the type hint and the returned labels drifting apart."""
    return_type = get_type_hints(RawEmail.preferred_body.fget)["return"]
    tuple_type = next(t for t in get_args(return_type) if t is not type(None))
    literal_type = get_args(tuple_type)[1]
    allowed = set(get_args(literal_type))

    for email in (make_email(text_body=None), make_email(html_body=None)):
        _, fmt = email.preferred_body
        # fmt should be one of the allowed literals
        assert fmt in allowed


# --------------------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------------------


def test_json_round_trip_preserves_the_email(make_email):
    original = make_email()
    restored = RawEmail.model_validate_json(original.model_dump_json())
    assert restored == original


def test_preferred_body_is_a_property_not_a_serialized_field(make_email):
    dumped = make_email().model_dump()
    assert "preferred_body" not in dumped
    assert set(dumped) == set(REQUIRED_FIELDS) | {"html_body", "text_body"}
