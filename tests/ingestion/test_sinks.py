"""Unit tests for JsonFileSink."""

import os

import pytest

from email_summarizer.ingestion.sinks import EmailSink, JsonFileSink
from email_summarizer.models.raw_email import RawEmail
from email_summarizer.utils import files


@pytest.fixture
def sink(tmp_path) -> JsonFileSink:
    return JsonFileSink(tmp_path / "raw")


def test_round_trip_loads_back_an_equal_email(sink, make_email):
    email = make_email(message_id="abc123", subject="Café ✓", html_body=None)

    sink.write(email)

    text = (sink.directory / "abc123.json").read_text(encoding="utf-8")
    assert RawEmail.model_validate_json(text) == email


def test_rewrite_is_idempotent_and_keeps_latest(sink, make_email):
    sink.write(make_email(message_id="abc123", subject="first"))
    sink.write(make_email(message_id="abc123", subject="second"))

    assert os.listdir(sink.directory) == ["abc123.json"]
    stored = RawEmail.model_validate_json((sink.directory / "abc123.json").read_text())
    assert stored.subject == "second"


def test_creates_missing_directory(tmp_path, make_email):
    sink = JsonFileSink(tmp_path / "data" / "raw")
    sink.write(make_email(message_id="abc123"))
    assert (tmp_path / "data" / "raw" / "abc123.json").is_file()


@pytest.mark.parametrize("bad_id", ["../x", "a/b", "", "msg-1", ".hidden", "a" * 65])
def test_rejects_ids_that_are_not_plain_alphanumeric(tmp_path, sink, make_email, bad_id):
    with pytest.raises(ValueError) as excinfo:
        sink.write(make_email(message_id=bad_id))

    # A fixed message: the caller's id is never echoed into errors or logs.
    assert str(excinfo.value) == "message id is not a plain alphanumeric Gmail id"
    assert list(tmp_path.rglob("*")) == []  # nothing written anywhere, not even the dir


def test_failed_write_leaves_no_temp_or_partial_file(sink, make_email, monkeypatch):
    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", boom)

    with pytest.raises(OSError):
        sink.write(make_email(message_id="abc123"))

    assert os.listdir(sink.directory) == []


def test_satisfies_email_sink_protocol(sink, make_email):
    def run(target: EmailSink, email: RawEmail) -> None:  # mypy checks the structural match
        target.write(email)

    run(sink, make_email(message_id="abc123"))
    assert (sink.directory / "abc123.json").is_file()
