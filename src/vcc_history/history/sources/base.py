from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from ..config import HistoryConfig
from ..errors import HistoryError
from ..models import Harness, SessionRecord


class HistorySource(Protocol):
    harness: Harness

    def collect(self, config: HistoryConfig, include_delegated: bool = False) -> list[SessionRecord]:
        ...

    def matches_cwd(self, record: SessionRecord, cwd: str, config: HistoryConfig) -> bool:
        ...


def parse_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_instant(value: str | None) -> str | None:
    parsed = parse_instant(value)
    if parsed is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def instant_bounds(values: Iterable[object]) -> tuple[str | None, str | None]:
    earliest: datetime | None = None
    latest: datetime | None = None
    for value in values:
        parsed = parse_instant(value if isinstance(value, str) else None)
        if parsed is None:
            continue
        if earliest is None or parsed < earliest:
            earliest = parsed
        if latest is None or parsed > latest:
            latest = parsed
    return (
        earliest.isoformat().replace("+00:00", "Z") if earliest else None,
        latest.isoformat().replace("+00:00", "Z") if latest else None,
    )


def snapshot_records(path: str | Path) -> Iterator[dict[str, object]]:
    with open(path, "rb") as fh:
        remaining = os.fstat(fh.fileno()).st_size
        while remaining > 0:
            raw_line = fh.readline(remaining)
            if not raw_line:
                break
            remaining -= len(raw_line)
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                yield value


def canonical_path(path: str | Path) -> str:
    expanded = os.path.expanduser(os.fspath(path))
    return os.path.normcase(os.path.realpath(os.path.abspath(expanded)))


def file_alias_keys(harness: Harness, path: str | Path, session_id: str | None) -> list[str]:
    keys = [f"path:{canonical_path(path)}"]
    if session_id:
        keys.append(f"session:{harness}:{session_id}")
    return keys


def finalize_record(record: SessionRecord) -> SessionRecord:
    created_at = normalize_instant(record.get("created_at"))
    updated_at = normalize_instant(record.get("updated_at"))
    record["created_at"] = created_at
    record["updated_at"] = updated_at
    record["timestamp"] = updated_at or created_at
    record["alias_keys"] = list(dict.fromkeys(record.get("alias_keys", [])))
    return record


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
