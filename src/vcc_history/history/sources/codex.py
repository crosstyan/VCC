from __future__ import annotations

import glob
import os
from pathlib import Path

from ..config import HistoryConfig
from ..json_utils import read_first_json, string_or_none
from ..models import SessionRecord
from .base import ExactCwdMixin


class CodexSource(ExactCwdMixin):
    harness = "codex"

    def collect(self, config: HistoryConfig, include_subagents: bool = False) -> list[SessionRecord]:
        sessions_dir = config.codex_home / "sessions"
        if not sessions_dir.is_dir():
            return []
        records: list[SessionRecord] = []
        pattern = str(sessions_dir / "**" / "*.jsonl")
        for path in glob.glob(pattern, recursive=True):
            record = record_from_path(path)
            if record:
                records.append(record)
        return records


def record_from_path(path: str) -> SessionRecord | None:
    obj = read_first_json(path)
    if not obj or obj.get("type") != "session_meta":
        return None
    payload_obj = obj.get("payload")
    payload = payload_obj if isinstance(payload_obj, dict) else {}
    return {
        "harness": "codex",
        "path": os.path.abspath(path),
        "session_id": string_or_none(payload.get("id")),
        "timestamp": string_or_none(payload.get("timestamp") or obj.get("timestamp")),
        "cwd": string_or_none(payload.get("cwd")),
        "title": None,
        "source": string_or_none(payload.get("source")),
        "provider": None,
        "model": None,
    }


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
