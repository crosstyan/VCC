from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from ..config import HistoryConfig
from ..errors import HistoryError
from ..models import Harness, SessionRecord


class HistorySource(Protocol):
    harness: Harness

    def collect(self, config: HistoryConfig, include_subagents: bool = False) -> list[SessionRecord]:
        ...

    def matches_cwd(self, record: SessionRecord, cwd: str, config: HistoryConfig) -> bool:
        ...


def canonical_path(path: str | Path) -> str:
    expanded = os.path.expanduser(os.fspath(path))
    return os.path.normcase(os.path.realpath(os.path.abspath(expanded)))


def ensure_private_cache_path(cache_root: Path, target: Path) -> None:
    directories = [cache_root]
    for part in target.relative_to(cache_root).parts:
        directories.append(directories[-1] / part)

    for index, directory in enumerate(directories):
        try:
            if directory.is_symlink():
                raise HistoryError(f"Refusing symlinked VCC cache directory: {directory}")
            directory.mkdir(parents=index == 0, mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        except OSError as error:
            raise HistoryError(
                f"Could not prepare private VCC cache directory {directory}: {error}"
            ) from error


class ExactCwdMixin:
    def matches_cwd(self, record: SessionRecord, cwd: str, config: HistoryConfig) -> bool:
        target = canonical_path(cwd)
        record_cwd = record.get("cwd")
        return bool(record_cwd and canonical_path(record_cwd) == target)
