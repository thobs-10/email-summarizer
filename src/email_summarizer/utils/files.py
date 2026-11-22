"""Small filesystem helpers shared by anything that persists files."""

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """Write UTF-8 text to `path` atomically, as an owner-only (0600) file.

    The text goes to a temp file in the same directory, which os.replace then swaps in, so
    a concurrent reader sees the old or the new file, never a partial one. Every missing
    parent directory is created as 0700 (existing ones keep their mode). On any failure the
    temp file is removed and the target is left untouched.

    Raises:
        OSError: If the directory can't be created or the file can't be written.
    """
    _make_private_dirs(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _make_private_dirs(directory: Path) -> None:
    """Create `directory` and any missing ancestors as 0700.

    mkdir(parents=True) applies `mode` only to the last directory, so the missing ones are
    created one by one, outermost first.
    """
    missing = [d for d in (directory, *directory.parents) if not d.exists()]
    for d in reversed(missing):
        d.mkdir(mode=0o700, exist_ok=True)
