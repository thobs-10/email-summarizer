"""Unit tests for SyncState and JsonStateStore."""

import os
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from email_summarizer.state.gmail_state import JsonStateStore, SyncState
from email_summarizer.utils import files


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


# ----------------------------------
# JsonStateStore
# ----------------------------------
@pytest.fixture
def store(tmp_path) -> JsonStateStore:
    return JsonStateStore(tmp_path / "state" / "gmail_sync.json")


@pytest.fixture
def synced() -> SyncState:
    return SyncState(history_id="98765", last_synced=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))


def test_missing_file_loads_default_state(store):
    assert store.load() == SyncState()


def test_save_load_round_trip(store, synced):
    store.save(synced)
    assert store.load() == synced


def test_save_creates_parent_dir(store, synced):
    assert not store.path.parent.exists()
    store.save(synced)
    assert store.path.is_file()


def test_save_overwrites_previous_state(store, synced):
    store.save(SyncState(history_id="1"))
    store.save(synced)
    assert store.load() == synced
    assert os.listdir(store.path.parent) == [store.path.name]


@pytest.mark.parametrize(
    "content", [b"{not json", b'{"history_id": ""}', b'{"unexpected": 1}', b"\xff\xfe\x00"]
)
def test_corrupt_file_fails_loudly(store, content):
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(content)
    with pytest.raises(ValidationError):
        store.load()


def test_failed_save_keeps_old_state_and_leaves_no_temp_file(store, synced, monkeypatch):
    store.save(synced)

    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", boom)

    with pytest.raises(OSError):
        store.save(SyncState(history_id="1"))

    assert store.load() == synced
    assert os.listdir(store.path.parent) == [store.path.name]
