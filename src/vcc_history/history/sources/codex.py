from __future__ import annotations

import glob
import os
from pathlib import Path

from ..config import HistoryConfig
from ..errors import HistoryStoreError, warn
from ..json_utils import string_or_none
from ..models import SessionRecord
from .base import ExactCwdMixin, file_alias_keys, finalize_record, instant_bounds, snapshot_records


class CodexSource(ExactCwdMixin):
    harness = "codex"

    def collect(self, config: HistoryConfig, include_delegated: bool = False) -> list[SessionRecord]:
        home = config.codex_home
        if not home.exists():
            return []
        try:
            with os.scandir(home):
                pass
        except OSError as error:
            raise HistoryStoreError("Codex", home, error) from error

        sessions_dir = home / "sessions"
        if not sessions_dir.exists():
            return []
        if not sessions_dir.is_dir():
            warn(sessions_dir, "expected a directory")
            return []
        try:
            with os.scandir(sessions_dir):
                pass
        except OSError as error:
            raise HistoryStoreError("Codex", sessions_dir, error) from error

        records: list[SessionRecord] = []
        pattern = str(sessions_dir / "**" / "*.jsonl")
        for path in glob.glob(pattern, recursive=True):
            record = record_from_path(path)
            if record:
                records.append(record)
        return records


def record_from_path(path: str) -> SessionRecord | None:
    try:
        records = snapshot_records(path)
        obj = next(records, None)
        if not obj or obj.get("type") != "session_meta":
            return None
        payload_obj = obj.get("payload")
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        timestamps = [obj.get("timestamp")]
        timestamps.extend(record.get("timestamp") for record in records)
    except OSError as error:
        warn(path, error)
        return None

    absolute_path = os.path.abspath(path)
    session_id = string_or_none(payload.get("id"))
    created_at = string_or_none(payload.get("timestamp") or obj.get("timestamp"))
    _, updated_at = instant_bounds(timestamps)
    return finalize_record(
        {
            "harness": "codex",
            "path": absolute_path,
            "session_id": session_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "kind": "main",
            "cwd": string_or_none(payload.get("cwd")),
            "title": None,
            "source": string_or_none(payload.get("source")),
            "provider": None,
            "model": None,
            "alias_keys": file_alias_keys("codex", absolute_path, session_id),
        }
    )


def rollout_for_session_id(session_id: str | None, config: HistoryConfig) -> Path | None:
    if not session_id:
        return None
    pattern = str(config.codex_home / "sessions" / "**" / f"*{session_id}*.jsonl")
    matches = sorted(glob.glob(pattern, recursive=True))
    for match in matches:
        path = Path(match)
        if path.is_file() and record_from_path(str(path)):
            return path.resolve()
    return None
