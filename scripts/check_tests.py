#!/usr/bin/env python3
"""Fail commits that change source code without accompanying test changes."""

from __future__ import annotations

import subprocess
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _is_source_file(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    if not path.startswith("src/"):
        return False
    if "/tests/" in path:
        return False
    return True


def _is_test_file(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    if "/tests/" not in path:
        return False
    file_name = Path(path).name
    return file_name.startswith("test_")


def _has_matching_test(source_path: str, staged_tests: list[str]) -> bool:
    """Return True when staged tests include a file mapped to the source module."""
    source = Path(source_path)

    if source.name == "__init__.py":
        # Package marker modules are validated by any staged test in the package.
        src_parts = source.parts
        if len(src_parts) >= 3:
            package_root = "/".join(src_parts[:3])
            return any(
                test.startswith(f"{package_root}/tests/") for test in staged_tests
            )
        return bool(staged_tests)

    stem = source.stem
    patterns = [f"*/tests/test_{stem}.py", f"*/tests/test_{stem}_*.py"]
    return any(
        any(fnmatch(test_path, pattern) for pattern in patterns)
        for test_path in staged_tests
    )


def main() -> int:
    try:
        staged = _staged_files()
    except subprocess.CalledProcessError as error:
        print(f"check-tests: failed to inspect staged files: {error}")
        return 2

    staged_source = [path for path in staged if _is_source_file(path)]
    if not staged_source:
        return 0

    staged_tests = [path for path in staged if _is_test_file(path)]
    missing = [
        source_path
        for source_path in staged_source
        if not _has_matching_test(source_path, staged_tests)
    ]
    if not missing:
        return 0

    print(
        "check-tests: every staged source file must have a matching staged test file."
    )
    print("missing matching tests for:")
    for path in missing:
        print(f"  - {path}")
    print("expected patterns:")
    print("  - src/**/tests/test_<module>.py")
    print("  - src/**/tests/test_<module>_*.py")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
