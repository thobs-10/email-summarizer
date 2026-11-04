"""Unit tests for write_text_atomic."""

import os
import stat
import sys

import pytest

from email_summarizer.utils import files
from email_summarizer.utils.files import write_text_atomic


def test_writes_utf8_text(tmp_path):
    path = tmp_path / "out.json"
    write_text_atomic(path, "café ✓")
    assert path.read_bytes() == "café ✓".encode()


def test_overwrites_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "out.json"
    path.write_text("old")

    write_text_atomic(path, "new")

    assert path.read_text() == "new"
    assert os.listdir(tmp_path) == ["out.json"]


def test_creates_missing_parent_dirs(tmp_path):
    path = tmp_path / "a" / "b" / "out.json"
    write_text_atomic(path, "x")
    assert path.read_text() == "x"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_file_is_owner_only_and_new_dirs_private(tmp_path):
    path = tmp_path / "outer" / "inner" / "out.json"
    write_text_atomic(path, "x")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for new_dir in (path.parent, path.parent.parent):
        assert stat.S_IMODE(new_dir.stat().st_mode) & 0o077 == 0  # umask may only tighten


def test_failed_replace_keeps_old_file_and_cleans_up(tmp_path, monkeypatch):
    path = tmp_path / "out.json"
    path.write_text("old")

    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        write_text_atomic(path, "new")

    assert path.read_text() == "old"
    assert os.listdir(tmp_path) == ["out.json"]


def test_failed_first_write_leaves_nothing(tmp_path, monkeypatch):
    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", boom)

    with pytest.raises(OSError):
        write_text_atomic(tmp_path / "out.json", "new")

    assert os.listdir(tmp_path) == []
