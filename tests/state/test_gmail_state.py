"""Unit tests for SyncState."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from email_summarizer.state.gmail_state import SyncState


def test_defaults_are_none():
    state = SyncState()
    assert state.history_id is None and state.last_synced is None


def test_naive_last_synced_is_rejected():
    with pytest.raises(ValidationError):
        SyncState(last_synced=datetime(2026, 1, 1, 12, 0))


def test_aware_last_synced_is_accepted():
    moment = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert SyncState(last_synced=moment).last_synced == moment


def test_is_frozen():
    state = SyncState()
    with pytest.raises(ValidationError):
        state.history_id = "123"


def test_json_round_trip():
    state = SyncState(history_id="98765", last_synced=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert SyncState.model_validate(state.model_dump(mode="json")) == state


def test_empty_history_id_is_rejected():
    with pytest.raises(ValidationError):
        SyncState(history_id="")


def test_unknown_keys_are_rejected():
    with pytest.raises(ValidationError):
        SyncState.model_validate({"history_id": "1", "unexpected": True})
