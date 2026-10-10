"""Tests for the Gmail allow-list query builder."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from email_summarizer.gmail.queries import DEFAULT_MAX_QUERY_LENGTH, build_sender_queries

# 2026-10-01T00:00:00Z in epoch seconds.
OCT_1_UTC = datetime(2026, 10, 1, tzinfo=UTC)
OCT_1_EPOCH = 1790812800


def test_single_sender_uses_from_group() -> None:
    assert build_sender_queries(["a@x.com"]) == ["from:(a@x.com)"]


def test_multiple_senders_joined_with_or_in_order() -> None:
    queries = build_sender_queries(["b@y.org", "a@x.com", "c@z.net"])

    assert queries == ["from:(b@y.org OR a@x.com OR c@z.net)"]


def test_after_clause_appended_when_given() -> None:
    assert build_sender_queries(["a@x.com"], OCT_1_UTC) == [f"from:(a@x.com) after:{OCT_1_EPOCH}"]


def test_after_clause_omitted_when_none() -> None:
    assert "after:" not in build_sender_queries(["a@x.com"], None)[0]


def test_non_utc_after_is_converted_to_utc_epoch() -> None:
    plus_two = datetime(2026, 10, 1, 2, 0, tzinfo=timezone(timedelta(hours=2)))

    assert build_sender_queries(["a@x.com"], plus_two)[0].endswith(f" after:{OCT_1_EPOCH}")


def test_fractional_seconds_are_floored() -> None:
    almost_next_second = OCT_1_UTC + timedelta(microseconds=999_999)

    assert build_sender_queries(["a@x.com"], almost_next_second)[0].endswith(f":{OCT_1_EPOCH}")


def test_naive_after_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_sender_queries(["a@x.com"], datetime(2026, 10, 1))


def test_empty_sender_list_rejected() -> None:
    with pytest.raises(ValueError, match="at least one sender"):
        build_sender_queries([], OCT_1_UTC)


@pytest.mark.parametrize("max_length", [60, 120, DEFAULT_MAX_QUERY_LENGTH])
def test_chunking_keeps_every_sender_once_in_order_within_limit(max_length: int) -> None:
    senders = [f"sender{i:02d}@example.com" for i in range(60)]

    queries = build_sender_queries(senders, OCT_1_UTC, max_length=max_length)

    assert all(len(query) <= max_length for query in queries)
    assert all(query.endswith(f" after:{OCT_1_EPOCH}") for query in queries)
    found = [s for q in queries for s in q.split(" after:")[0][6:-1].split(" OR ")]
    assert found == senders


def test_default_limit_splits_a_long_allow_list() -> None:
    senders = [f"sender{i:03d}@example.com" for i in range(100)]

    assert len(build_sender_queries(senders)) > 1


def test_two_senders_that_exactly_fit_share_one_query() -> None:
    query = "from:(a@x.com OR b@y.org)"

    assert build_sender_queries(["a@x.com", "b@y.org"], max_length=len(query)) == [query]
    assert build_sender_queries(["a@x.com", "b@y.org"], max_length=len(query) - 1) == [
        "from:(a@x.com)",
        "from:(b@y.org)",
    ]


def test_single_sender_that_exactly_fits_is_accepted() -> None:
    query = f"from:(a@x.com) after:{OCT_1_EPOCH}"

    assert build_sender_queries(["a@x.com"], OCT_1_UTC, max_length=len(query)) == [query]


def test_oversize_single_sender_rejected() -> None:
    senders = ["a@x.com", "a-very-long-newsletter-address@example.com"]

    with pytest.raises(ValueError, match="sender #2 cannot fit"):
        build_sender_queries(senders, max_length=30)


@pytest.mark.parametrize("max_length", [-1, 0, len(f"from:() after:{OCT_1_EPOCH}")])
def test_max_length_too_small_for_prefix_and_after_clause_rejected(max_length: int) -> None:
    with pytest.raises(ValueError, match="max_length is too small"):
        build_sender_queries(["a@x.com"], OCT_1_UTC, max_length=max_length)


def test_after_before_unix_epoch_rejected() -> None:
    with pytest.raises(ValueError, match="before 1970"):
        build_sender_queries(["a@x.com"], datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC))


def test_after_at_unix_epoch_accepted() -> None:
    epoch_start = datetime(1970, 1, 1, tzinfo=UTC)

    assert build_sender_queries(["a@x.com"], epoch_start) == ["from:(a@x.com) after:0"]


def test_upper_case_ascii_sender_accepted() -> None:
    assert build_sender_queries(["News@Example.COM"]) == ["from:(News@Example.COM)"]


@pytest.mark.parametrize(
    "sender",
    [
        "a@x.com OR from:bank",
        "a@x.com)",
        '"a@x.com"',
        "a b@x.com",
        "{a@x.com}",
        "",
        "-a@x.com",  # a leading "-" negates a Gmail search term
        "a@x.com\n",
        "not-an-address",
        "a@x.co\u0131",  # dotless i: Unicode case-folding would match "i"
        "\u0130nfo@x.com",  # dotted capital I
        "\u017fales@x.com",  # long s: case-folds to "s"
        "a@\u212aelvin.com",  # Kelvin sign: case-folds to "k"
    ],
)
def test_unsafe_sender_rejected_without_echoing_it(sender: str) -> None:
    with pytest.raises(ValueError, match="sender #2 is not a plain email address") as exc_info:
        build_sender_queries(["ok@x.com", sender])

    if sender:
        assert sender not in str(exc_info.value)
