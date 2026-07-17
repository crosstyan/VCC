from __future__ import annotations

import fnmatch
import json
import os
import stat
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from ..config import HistoryConfig
from ..json_utils import string_or_none
from ..models import SessionRecord
from .base import ExactCwdMixin


class ClaudeSource(ExactCwdMixin):
    harness = "claude"

    def collect(self, config: HistoryConfig, include_delegated: bool = False) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        for path in _session_paths(config, include_delegated):
            record = record_from_path(path)
            if record:
                records.append(record)
        return records


def record_from_path(path: str) -> SessionRecord | None:
    path_obj = Path(path)
    source = "subagent" if _is_subagent_path(path_obj) else None
    session_id: str | None = None
    cwd: str | None = None
    ai_title: str | None = None
    custom_title: str | None = None
    model: str | None = None
    latest_timestamp: tuple[datetime, str] | None = None
    has_conversation_message = False

    try:
        records = _snapshot_records(path)
        for obj in records:
            message = obj.get("message")
            if (
                obj.get("type") in {"user", "assistant"}
                and isinstance(message, dict)
                and _has_conversation_content(message)
            ):
                has_conversation_message = True

            if session_id is None:
                session_id = _nonempty_string(obj.get("sessionId")) or _nonempty_string(
                    obj.get("session_id")
                )
            if cwd is None:
                cwd = _nonempty_string(obj.get("cwd"))

            timestamp = _nonempty_string(obj.get("timestamp"))
            parsed_timestamp = _parse_timestamp(timestamp)
            if timestamp and parsed_timestamp and (
                latest_timestamp is None or parsed_timestamp >= latest_timestamp[0]
            ):
                latest_timestamp = (parsed_timestamp, timestamp)

            if isinstance(message, dict):
                nested_model = _nonempty_string(message.get("model"))
                if nested_model and nested_model != "<synthetic>":
                    model = nested_model

            if obj.get("type") == "ai-title":
                value = _nonempty_string(obj.get("aiTitle"))
                if value:
                    ai_title = value
            elif obj.get("type") == "custom-title":
                value = _nonempty_string(obj.get("customTitle"))
                if value:
                    custom_title = value
    except OSError:
        return None

    if not has_conversation_message:
        return None

    if source == "subagent":
        session_id = path_obj.stem.removeprefix("agent-")

    return {
        "harness": "claude",
        "path": os.path.abspath(path),
        "session_id": session_id,
        "timestamp": latest_timestamp[1] if latest_timestamp else None,
        "cwd": cwd,
        "title": custom_title or ai_title,
        "source": source,
        "provider": None,
        "model": model,
    }


def _snapshot_records(path: str) -> Iterator[dict[str, object]]:
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


def _session_paths(config: HistoryConfig, include_delegated: bool) -> list[str]:
    projects_dir = config.claude_config_dir / "projects"
    if not projects_dir.is_dir():
        return []

    candidates: list[Path] = []
    try:
        with os.scandir(projects_dir) as project_entries:
            for project_entry in project_entries:
                if not project_entry.is_dir(follow_symlinks=False):
                    continue
                project_dir = Path(project_entry.path)
                candidates.extend(_main_transcripts(project_dir))
                if include_delegated:
                    candidates.extend(_subagent_transcripts(project_dir))
    except OSError:
        return []

    return [str(path) for path in _deduplicate_aliases(candidates)]


def _main_transcripts(project_dir: Path) -> list[Path]:
    try:
        with os.scandir(project_dir) as entries:
            return [
                Path(entry.path)
                for entry in entries
                if entry.name.endswith(".jsonl") and entry.is_file(follow_symlinks=True)
            ]
    except OSError:
        return []


def _subagent_transcripts(project_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project_dir, followlinks=False):
        dirnames[:] = [
            dirname for dirname in dirnames if not (Path(dirpath) / dirname).is_symlink()
        ]
        for filename in filenames:
            path = Path(dirpath) / filename
            if _is_subagent_path(path.relative_to(project_dir)) and path.is_file():
                paths.append(path)
    return paths


def _deduplicate_aliases(candidates: list[Path]) -> list[Path]:
    ordered = sorted(
        candidates,
        key=lambda path: (path.is_symlink(), _is_subagent_path(path), str(path)),
    )
    seen_paths: set[str] = set()
    seen_files: set[tuple[int, int]] = set()
    result: list[Path] = []

    for path in ordered:
        absolute = Path(os.path.abspath(path))
        path_key = os.path.normcase(str(absolute))
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)

        file_key = _file_identity(absolute)
        if file_key is not None and file_key in seen_files:
            continue
        if file_key is not None:
            seen_files.add(file_key)
        result.append(absolute)

    return result


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        value = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(value.st_mode) or value.st_ino == 0:
        return None
    return value.st_dev, value.st_ino


def _has_conversation_content(message: dict[str, object]) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    return isinstance(content, list) and bool(content)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _nonempty_string(value: object) -> str | None:
    text = string_or_none(value)
    return text if text else None


def _is_subagent_path(path: Path) -> bool:
    return "subagents" in path.parts and fnmatch.fnmatchcase(path.name, "agent-*.jsonl")
