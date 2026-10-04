"""Turn a Gmail API message (format="full") into a RawEmail.

Pure: no API calls and no logging, so email content never reaches the logs.
"""

import base64
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.utils import parseaddr
from typing import Any

from pydantic import ValidationError

from email_summarizer.models.raw_email import RawEmail

GmailPart = dict[str, Any]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class MessageParseError(ValueError):
    """A Gmail message lacks data needed to build a RawEmail; skip it rather than fail a sync.

    The message carries only the id and a content-free reason, so it is safe to log.
    """

    def __init__(self, message_id: str | None, reason: str) -> None:
        super().__init__(f"Cannot parse Gmail message {message_id!r}: {reason}")
        self.message_id = message_id


def parse_message(message: GmailPart) -> RawEmail:
    """Build a RawEmail from a Gmail message resource fetched with format="full".

    Raises:
        MessageParseError: If required fields are missing or malformed.
    """
    message_id = message.get("id")
    try:
        return _build(message)
    except ValidationError as exc:  # report field names only; str(exc) would echo the values
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise MessageParseError(message_id, f"invalid fields: {fields}") from exc
    except KeyError as exc:
        raise MessageParseError(message_id, f"missing field {exc.args[0]!r}") from exc
    except ValueError as exc:  # our own content-free messages, or base64 decoding errors
        raise MessageParseError(message_id, str(exc)) from exc
    except (TypeError, AttributeError) as exc:  # malformed structure, e.g. non-string header
        raise MessageParseError(message_id, f"malformed message ({type(exc).__name__})") from exc


def _build(message: GmailPart) -> RawEmail:
    """Map the Gmail message fields onto RawEmail; errors are translated by parse_message."""
    payload = message["payload"]
    headers = _headers(payload)
    sender_name, sender_email = _sender(headers.get("from", ""))
    return RawEmail(
        message_id=message["id"],
        thread_id=message["threadId"],
        label_ids=message.get("labelIds", []),
        sender_id=sender_name or sender_email,
        sender_email=sender_email,
        received_at=_received_at(message["internalDate"]),
        subject=headers.get("subject", ""),
        html_body=_find_body(payload, "text/html"),
        text_body=_find_body(payload, "text/plain"),
    )


def _headers(part: GmailPart) -> dict[str, str]:
    """Header names lower-cased to their values (Gmail already decodes RFC 2047 words).

    The first occurrence wins, as in email.message.Message, so a second spoofed From header
    can't replace the real one before the allow-list check.
    """
    headers: dict[str, str] = {}
    for header in part.get("headers", []):
        headers.setdefault(header["name"].lower(), header["value"])
    return headers


def _sender(from_header: str) -> tuple[str, str]:
    """Split a From header into (display name, lower-cased address)."""
    name, address = parseaddr(from_header)
    if "@" not in address:
        raise ValueError("From header has no sender address")
    return name.strip(), address.lower()


def _received_at(internal_date: str) -> datetime:
    """Convert Gmail's internalDate (server-assigned epoch ms) to UTC.

    The Date header is sender-controlled, so it is deliberately not used.
    """
    try:
        return _EPOCH + timedelta(milliseconds=int(internal_date))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("internalDate is not a valid epoch-milliseconds timestamp") from exc


def _iter_parts(part: GmailPart) -> Iterator[GmailPart]:
    """Yield the part and all nested parts, depth-first in document order.

    Iterative rather than recursive, so a maliciously deep MIME tree can't raise
    RecursionError and abort a whole sync.
    """
    stack = [part]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.get("parts", [])))  # reversed keeps document order


def _find_body(payload: GmailPart, mime_type: str) -> str | None:
    """Decode the first non-attachment part of the given type, or None if there is none."""
    for part in _iter_parts(payload):
        body = part.get("body", {})
        is_attachment = bool(part.get("filename")) or "attachmentId" in body
        if part.get("mimeType") == mime_type and body.get("data") and not is_attachment:
            return _decode(body["data"], _charset(part))
    return None


def _charset(part: GmailPart) -> str:
    """Charset from the part's Content-Type header, defaulting to UTF-8."""
    content_type = Message()
    content_type["Content-Type"] = _headers(part).get("content-type", "text/plain")
    return content_type.get_content_charset() or "utf-8"


def _decode(data: str, charset: str) -> str:
    """Decode Gmail's base64url body data, tolerating missing padding and unknown charsets."""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except ValueError as exc:  # binascii.Error
        raise ValueError("body data is not valid base64url") from exc
    try:
        return raw.decode(charset, errors="replace")
    except (LookupError, UnicodeError):  # unknown charset, or a codec that can't "replace"
        return raw.decode("utf-8", errors="replace")
