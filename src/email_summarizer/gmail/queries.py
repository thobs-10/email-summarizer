"""Build Gmail search queries for the newsletter allow-list.

Pure: no API calls, no logging and no settings import, so it is trivial to test and safe to
call from anywhere. The sync service passes the results to GmailClient.get_message_ids.
"""

import math
from collections.abc import Sequence
from datetime import datetime

from email_summarizer.addresses import SAFE_ADDRESS

DEFAULT_MAX_QUERY_LENGTH = 1000


def build_sender_queries(
    senders: Sequence[str],
    after: datetime | None = None,
    *,
    max_length: int = DEFAULT_MAX_QUERY_LENGTH,
) -> list[str]:
    """Build queries like `from:(a@x.com OR b@y.org) after:1790640000` covering every sender.

    Senders are split across as many queries as needed to keep each one within max_length
    characters. Order is preserved and each sender appears in exactly one query.

    Args:
        senders: Exact sender addresses (the allow-list).
        after: Only match messages received after this timezone-aware moment; None for no limit.
        max_length: Maximum length of each query string, including the after: clause.

    Raises:
        ValueError: If senders is empty, a sender is not a plain address, after is naive or
            before 1970, max_length is too small, or a single sender cannot fit within it.

    Returns:
        A list of query strings, each covering a subset of the senders and including the after: clause if specified.
    """
    if not senders:  # an empty from:() could match every message in the inbox
        raise ValueError("at least one sender is required")
    for position, sender in enumerate(senders, start=1):
        _validate_sender(position, sender)
    suffix = _after_clause(after)
    if max_length <= len(_query([], suffix)):
        raise ValueError("max_length is too small for the query prefix and after: clause")
    return [_query(chunk, suffix) for chunk in _chunk(senders, suffix, max_length)]


def _validate_sender(position: int, sender: str) -> None:
    """Reject anything that is not a plain address safe to embed in a query.

    The error names the position, not the value, so it never echoes untrusted input.
    """
    if not SAFE_ADDRESS.fullmatch(sender):
        raise ValueError(f"sender #{position} is not a plain email address")


def _after_clause(after: datetime | None) -> str:
    """Return ` after:<epoch seconds>`, or an empty string when after is None.

    Gmail reads a bare date like after:2026/10/01 in the account's own timezone, but epoch
    seconds are an exact instant, so there is no timezone guesswork.
    """
    if after is None:
        return ""
    if after.utcoffset() is None:
        raise ValueError("after must be timezone-aware")
    # Convert the timezone-aware datetime to epoch seconds.
    epoch = math.floor(after.timestamp())
    if epoch < 0:  # Gmail would misread a negative epoch such as after:-315619200
        raise ValueError("after must not be before 1970-01-01 UTC")
    return f" after:{epoch}"


def _query(senders: Sequence[str], suffix: str) -> str:
    """Join senders into one from:(...) term and append the after: clause."""
    return f"from:({' OR '.join(senders)}){suffix}"


def _chunk(senders: Sequence[str], suffix: str, max_length: int) -> list[list[str]]:
    """Greedily group senders, in order, so each group's query fits within max_length."""
    chunks: list[list[str]] = []
    current: list[str] = []
    for position, sender in enumerate(senders, start=1):
        if len(_query([sender], suffix)) > max_length:
            raise ValueError(f"sender #{position} cannot fit in a {max_length}-character query")
        if current and len(_query([*current, sender], suffix)) > max_length:
            chunks.append(current)
            current = []
        current.append(sender)
    chunks.append(current)
    return chunks
