"""Tests for parse_message: Gmail API payload (format="full") -> RawEmail.

Payload builders and sample content live in tests/gmail/conftest.py.
"""

from datetime import UTC, datetime

import pytest

from email_summarizer.gmail.parser import MessageParseError, parse_message

# ----------------------------------
# metadata
# ----------------------------------


def test_metadata_fields(linkedin_message, linkedin_bodies):
    email = parse_message(linkedin_message)

    assert email.message_id == "msg-1"
    assert email.thread_id == "thread-1"
    assert email.label_ids == ["INBOX", "CATEGORY_UPDATES"]
    assert email.subject == linkedin_bodies["subject"]


def test_received_at_comes_from_internal_date_in_utc(linkedin_message):
    email = parse_message(linkedin_message)

    assert email.received_at == datetime(2026, 9, 29, 16, 27, 0, 123000, tzinfo=UTC)


def test_sender_display_name_and_address(linkedin_message):
    email = parse_message(linkedin_message)

    assert email.sender_id == "Towards AI, Inc. via LinkedIn"
    assert email.sender_email == "newsletters-noreply@linkedin.com"


def test_sender_id_falls_back_to_address_and_address_is_lowercased(make_part, make_message):
    message = make_message(
        make_part("text/plain", "hi"), headers={"From": "News@Example.COM", "Subject": "s"}
    )

    email = parse_message(message)

    assert email.sender_email == "news@example.com"
    assert email.sender_id == "news@example.com"


def test_header_names_are_case_insensitive(make_part, make_message):
    message = make_message(
        make_part("text/plain", "hi"), headers={"from": "a@x.com", "SUBJECT": "Hello"}
    )

    email = parse_message(message)

    assert (email.sender_email, email.subject) == ("a@x.com", "Hello")


def test_missing_subject_defaults_to_empty(make_part, make_message):
    message = make_message(make_part("text/plain", "hi"), headers={"From": "a@x.com"})

    assert parse_message(message).subject == ""


def test_missing_label_ids_default_to_empty(linkedin_message):
    del linkedin_message["labelIds"]

    assert parse_message(linkedin_message).label_ids == []


# ----------------------------------
# body selection
# ----------------------------------


def test_alternative_message_has_both_bodies(linkedin_message, linkedin_bodies):
    email = parse_message(linkedin_message)

    assert email.html_body == linkedin_bodies["html"]
    assert email.text_body == linkedin_bodies["text"]
    assert email.preferred_body == (linkedin_bodies["html"], "html")


def test_single_part_text_only(make_part, make_message):
    email = parse_message(make_message(make_part("text/plain", "Plain only")))

    assert email.text_body == "Plain only"
    assert email.html_body is None


def test_single_part_html_only(make_part, make_message):
    email = parse_message(make_message(make_part("text/html", "<p>HTML only</p>")))

    assert email.html_body == "<p>HTML only</p>"
    assert email.text_body is None


def test_nested_mixed_skips_attachments(make_part, make_message):
    payload = make_part(
        "multipart/mixed",
        parts=[
            make_part(
                "multipart/alternative",
                parts=[make_part("text/plain", "body text"), make_part("text/html", "<p>body</p>")],
            ),
            make_part("application/pdf", filename="report.pdf", attachment_id="att-1"),
            # A text attachment with inline data must not be mistaken for the body.
            make_part("text/html", "<p>attached page</p>", filename="page.html"),
        ],
    )

    email = parse_message(make_message(payload))

    assert email.html_body == "<p>body</p>"
    assert email.text_body == "body text"


def test_related_with_inline_image(make_part, make_message):
    payload = make_part(
        "multipart/related",
        parts=[
            make_part("text/html", '<img src="cid:logo"><p>Issue 1</p>'),
            make_part("image/png", filename="logo.png", attachment_id="att-logo"),
        ],
    )

    email = parse_message(make_message(payload))

    assert email.html_body == '<img src="cid:logo"><p>Issue 1</p>'


def test_message_without_any_body(make_part, make_message):
    payload = make_part("multipart/mixed", parts=[make_part("image/png", attachment_id="a")])

    email = parse_message(make_message(payload))

    assert email.html_body is None
    assert email.text_body is None
    assert email.preferred_body is None


# ----------------------------------
# decoding
# ----------------------------------


def test_non_utf8_charset_is_decoded(make_part, make_message):
    part = make_part("text/plain", "Café crème", charset="iso-8859-1")

    assert parse_message(make_message(part)).text_body == "Café crème"


def test_unknown_charset_falls_back_to_utf8(make_part, make_message):
    part = make_part("text/plain", "hello", charset="utf-8")
    part["headers"] = [{"name": "Content-Type", "value": 'text/plain; charset="x-made-up"'}]

    assert parse_message(make_message(part)).text_body == "hello"


def test_missing_charset_defaults_to_utf8(make_part, make_message):
    part = make_part("text/plain", "naïve")
    part["headers"] = []

    assert parse_message(make_message(part)).text_body == "naïve"


def test_unpadded_base64_is_decoded(make_part, make_message):
    part = make_part("text/plain", "ab", pad=False)  # "ab" -> "YWI" without "="

    assert parse_message(make_message(part)).text_body == "ab"


def test_undecodable_bytes_are_replaced_not_fatal(make_part, make_message):
    part = make_part("text/plain", data="_w")  # base64url for the single byte 0xFF

    assert parse_message(make_message(part)).text_body == "�"


# ----------------------------------
# errors
# ----------------------------------


@pytest.mark.parametrize("from_header", [None, "", "Just A Name", "not-an-address"])
def test_missing_or_bad_sender_raises(make_part, make_message, from_header):
    headers = {"Subject": "s"} if from_header is None else {"From": from_header, "Subject": "s"}
    message = make_message(make_part("text/plain", "hi"), headers=headers)

    with pytest.raises(MessageParseError, match="msg-1"):
        parse_message(message)


@pytest.mark.parametrize("missing", ["id", "threadId", "internalDate", "payload"])
def test_missing_required_field_raises(linkedin_message, missing):
    del linkedin_message[missing]

    with pytest.raises(MessageParseError):
        parse_message(linkedin_message)


def test_invalid_base64_raises(make_part, make_message):
    part = make_part("text/plain", data="a")  # a single base64 character can never be valid

    with pytest.raises(MessageParseError, match="msg-1.*not valid base64url"):
        parse_message(make_message(part))


@pytest.mark.parametrize("internal_date", ["yesterday", "99999999999999999999", None])
def test_invalid_internal_date_raises(linkedin_message, internal_date):
    linkedin_message["internalDate"] = internal_date

    with pytest.raises(MessageParseError, match="internalDate"):
        parse_message(linkedin_message)


def test_malformed_header_raises(linkedin_message):
    linkedin_message["payload"]["headers"].append({"name": None, "value": "x"})

    with pytest.raises(MessageParseError, match="malformed"):
        parse_message(linkedin_message)


# ----------------------------------
# errors are safe to log
# ----------------------------------


def test_error_exposes_message_id(make_part, make_message):
    message = make_message(make_part("text/plain", "hi"), headers={"Subject": "s"})

    with pytest.raises(MessageParseError) as exc:
        parse_message(message)

    assert exc.value.message_id == "msg-1"


def test_bad_sender_error_does_not_echo_header(make_part, make_message):
    message = make_message(make_part("text/plain", "hi"), headers={"From": "Victim Name"})

    with pytest.raises(MessageParseError) as exc:
        parse_message(message)

    assert "Victim" not in str(exc.value)


def test_validation_error_reports_fields_not_values(linkedin_message):
    linkedin_message["labelIds"] = [{"x": "victim@example.com"}]

    with pytest.raises(MessageParseError, match="label_ids") as exc:
        parse_message(linkedin_message)

    assert "victim@example.com" not in str(exc.value)


# ----------------------------------
# hardening
# ----------------------------------


def test_deeply_nested_mime_tree_does_not_overflow(make_part, make_message):
    payload = make_part("text/html", "<p>deep</p>")
    for _ in range(5000):  # far beyond Python's default recursion limit (1000)
        payload = make_part("multipart/mixed", parts=[payload])

    assert parse_message(make_message(payload)).html_body == "<p>deep</p>"


def test_first_from_header_wins(make_part, make_message):
    message = make_message(make_part("text/plain", "hi"), headers={"From": "good@substack.com"})
    message["payload"]["headers"].append({"name": "From", "value": "evil@x.com"})

    assert parse_message(message).sender_email == "good@substack.com"


def test_codec_without_replace_support_falls_back_to_utf8(make_part, make_message):
    part = make_part("text/plain", "hello")
    part["headers"] = [{"name": "Content-Type", "value": 'text/plain; charset="idna"'}]

    assert parse_message(make_message(part)).text_body == "hello"
