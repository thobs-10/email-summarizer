from __future__ import annotations

import pytest

from email_summarizer.utils.retry import RetryError, retry_with_backoff


def test_retry_with_backoff_succeeds_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def flaky_operation() -> str:
        calls["count"] += 1
        if calls["count"] < 2:
            raise TimeoutError("transient timeout")
        return "ok"

    monkeypatch.setattr("email_summarizer.utils.retry.time.sleep", lambda _: None)

    result = retry_with_backoff(
        operation=flaky_operation,
        exceptions=(TimeoutError,),
        max_attempts=3,
        base_delay_seconds=0.01,
        backoff_multiplier=2.0,
        operation_name="flaky operation",
    )

    assert result == "ok"
    assert calls["count"] == 2


def test_retry_with_backoff_raises_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def always_fails() -> None:
        calls["count"] += 1
        raise TimeoutError("still failing")

    monkeypatch.setattr("email_summarizer.utils.retry.time.sleep", lambda _: None)

    with pytest.raises(
        RetryError, match="always failing operation failed after 3 attempts"
    ) as exc_info:
        retry_with_backoff(
            operation=always_fails,
            exceptions=(TimeoutError,),
            max_attempts=3,
            base_delay_seconds=0.01,
            backoff_multiplier=2.0,
            operation_name="always failing operation",
        )

    assert calls["count"] == 3
    assert isinstance(exc_info.value.__cause__, TimeoutError)
