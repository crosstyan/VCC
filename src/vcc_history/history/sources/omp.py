from __future__ import annotations

import glob
import json
import os

from ..config import HistoryConfig
from ..json_utils import read_first_json, split_model_ref, string_or_none
from ..models import SessionRecord
from .base import ExactCwdMixin


class OmpSource(ExactCwdMixin):
    harness = "omp"

    def collect(self, config: HistoryConfig, include_subagents: bool = False) -> list[SessionRecord]:
        sessions_dir = config.omp_agent_dir / "sessions"
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
    if not obj or obj.get("type") != "session":
        return None
    model = _read_model_change(path) or string_or_none(obj.get("modelId") or obj.get("model"))
    provider, model_id = split_model_ref(model)
    return {
        "harness": "omp",
        "path": os.path.abspath(path),
        "session_id": string_or_none(obj.get("id")),
        "timestamp": string_or_none(obj.get("timestamp")),
        "cwd": string_or_none(obj.get("cwd")),
        "title": string_or_none(obj.get("title")),
        "source": None,
        "provider": provider,
        "model": model_id,
    }


def _read_model_change(path: str, limit: int = 64) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            for index, line in enumerate(fh):
                if index >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                value = json.loads(line)
                if not isinstance(value, dict) or value.get("type") != "model_change":
                    continue
                return string_or_none(value.get("model"))
    except (OSError, ValueError, TypeError):
        return None
    return None
