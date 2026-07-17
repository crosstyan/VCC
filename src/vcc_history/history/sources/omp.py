from __future__ import annotations

import glob
import os

from ..config import HistoryConfig
from ..errors import HistoryStoreError, warn
from ..json_utils import split_model_ref, string_or_none
from ..models import SessionRecord
from .base import ExactCwdMixin, file_alias_keys, finalize_record, instant_bounds, snapshot_records


class OmpSource(ExactCwdMixin):
    harness = "omp"

    def collect(self, config: HistoryConfig, include_delegated: bool = False) -> list[SessionRecord]:
        home = config.omp_agent_dir
        if not home.exists():
            return []
        try:
            with os.scandir(home):
                pass
        except OSError as error:
            raise HistoryStoreError("OMP", home, error) from error

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
            raise HistoryStoreError("OMP", sessions_dir, error) from error

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
        if not obj or obj.get("type") != "session":
            return None
        timestamps = [obj.get("timestamp")]
        model_change: str | None = None
        for index, record in enumerate(records, start=1):
            timestamps.append(record.get("timestamp"))
            if index < 64 and model_change is None and record.get("type") == "model_change":
                model_change = string_or_none(record.get("model"))
    except OSError as error:
        warn(path, error)
        return None

    model = model_change or string_or_none(obj.get("modelId") or obj.get("model"))
    provider, model_id = split_model_ref(model)
    absolute_path = os.path.abspath(path)
    session_id = string_or_none(obj.get("id"))
    _, updated_at = instant_bounds(timestamps)
    return finalize_record(
        {
            "harness": "omp",
            "path": absolute_path,
            "session_id": session_id,
            "created_at": string_or_none(obj.get("timestamp")),
            "updated_at": updated_at,
            "kind": "main",
            "cwd": string_or_none(obj.get("cwd")),
            "title": string_or_none(obj.get("title")),
            "source": None,
            "provider": provider,
            "model": model_id,
            "alias_keys": file_alias_keys("omp", absolute_path, session_id),
        }
    )
