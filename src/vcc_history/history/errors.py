from __future__ import annotations

import os
import sys
from collections.abc import Hashable
from pathlib import Path


_WARNED: set[Hashable] = set()


class HistoryError(RuntimeError):
    """Raised when a history source or compiler bridge cannot complete."""


class HistoryStoreError(HistoryError):
    """Raised when an existing history store cannot be read."""

    def __init__(self, harness: str, path: str | Path, reason: object) -> None:
        self.harness = harness
        self.path = str(path)
        self.reason = str(reason)
        super().__init__(
            f"Could not read {harness} history store {self.path}: {self.reason}"
        )


def warn(path: str | Path, message: object) -> None:
    print(f"warning: {path}: {message}", file=sys.stderr)


def warn_once(
    path: str | Path,
    message: object,
    key: Hashable | None = None,
) -> None:
    warning_key = key if key is not None else (str(path), str(message))
    if warning_key in _WARNED:
        return
    _WARNED.add(warning_key)
    warn(path, message)


def warn_malformed_json_record(
    path: str | Path,
    line_number: int,
    error: object,
) -> None:
    canonical_path = os.path.normcase(
        os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(path))))
    )
    error_text = str(error)
    warn_once(
        f"{path}:{line_number}",
        f"malformed JSON record: {error_text}",
        key=(canonical_path, line_number, "malformed-json-record", error_text),
    )
