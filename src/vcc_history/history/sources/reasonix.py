from __future__ import annotations

import glob
import os
import re
from pathlib import Path

from ..config import HistoryConfig
from ..errors import HistoryStoreError, warn
from ..json_utils import read_first_json, read_json_file, split_model_ref, string_or_none
from ..models import SessionRecord
from .base import canonical_path, file_alias_keys, finalize_record


class ReasonixSource:
    harness = "reasonix"

    def collect(self, config: HistoryConfig, include_delegated: bool = False) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        for path in _session_paths(config, include_delegated):
            record = _record_from_path(path, config)
            if record:
                records.append(record)
        return records

    def matches_cwd(self, record: SessionRecord, cwd: str, config: HistoryConfig) -> bool:
        target = canonical_path(cwd)
        record_cwd = record.get("cwd")
        if record_cwd and canonical_path(record_cwd) == target:
            return True
        return _project_slug(record["path"], config) in _workspace_slug_candidates(cwd)


def _cleanup_pending(path: str) -> bool:
    return Path(path).with_suffix(".cleanup-pending.json").exists()


def _model_from_filename(path: str) -> str | None:
    stem = Path(path).stem
    match = re.match(r"^\d{8}-\d{6}(?:\.\d+)?-(.+)$", stem)
    return match.group(1) if match else None


def _timestamp_from_filename(path: str) -> str | None:
    stem = Path(path).stem
    match = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(\.\d+)?-", stem)
    if not match:
        return None
    frac = match.group(7) or ""
    return (
        f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        f"T{match.group(4)}:{match.group(5)}:{match.group(6)}{frac}Z"
    )


def _workspace_slug(cwd: str) -> str:
    return re.sub(r"[/\\\\:]", "-", os.path.abspath(cwd))


def _workspace_slug_candidates(cwd: str) -> set[str]:
    return {_workspace_slug(cwd), _workspace_slug(canonical_path(cwd))}


def _project_slug(path: str, config: HistoryConfig) -> str | None:
    try:
        rel = Path(path).resolve().relative_to((config.reasonix_home / "projects").resolve())
    except ValueError:
        rel = None
    if rel and rel.parts:
        return rel.parts[0]
    parts = Path(path).parts
    for index, part in enumerate(parts):
        if part == "projects" and index + 1 < len(parts):
            return parts[index + 1]
    return None


def _sidecar(path: str) -> dict[str, object]:
    jsonl_path = Path(path)
    if jsonl_path.parent.name == "subagents":
        return read_json_file(jsonl_path.with_suffix(".meta.json"))
    return read_json_file(Path(path + ".meta"))


def _parent_cleanup_pending(path: str, meta: dict[str, object]) -> bool:
    jsonl_path = Path(path)
    if jsonl_path.parent.name != "subagents":
        return False
    parent_session = string_or_none(meta.get("parentSession"))
    if not parent_session:
        return False
    parent_path = jsonl_path.parent.parent / f"{parent_session}.jsonl"
    return parent_path.with_suffix(".cleanup-pending.json").exists()


def _record_from_path(path: str, config: HistoryConfig) -> SessionRecord | None:
    if _cleanup_pending(path):
        return None
    obj = read_first_json(path)
    if not obj or obj.get("role") not in {"system", "user", "assistant", "tool"}:
        return None
    meta = _sidecar(path)
    if _parent_cleanup_pending(path, meta):
        return None
    model = string_or_none(meta.get("model")) or string_or_none(meta.get("Model")) or _model_from_filename(path)
    provider, model_id = split_model_ref(model)
    session_id = string_or_none(meta.get("id")) or string_or_none(meta.get("ref")) or Path(path).stem
    created_at = (
        string_or_none(meta.get("created_at"))
        or string_or_none(meta.get("createdAt"))
        or _timestamp_from_filename(path)
    )
    updated_at = string_or_none(meta.get("updated_at")) or string_or_none(
        meta.get("updatedAt")
    )
    title = (
        string_or_none(meta.get("topic_title"))
        or string_or_none(meta.get("name"))
        or string_or_none(meta.get("preview"))
    )
    cwd = string_or_none(meta.get("workspace_root") or meta.get("workspaceRoot"))
    absolute_path = os.path.abspath(path)
    kind = "delegated" if Path(path).parent.name == "subagents" else "main"
    return finalize_record(
        {
            "harness": "reasonix",
            "path": absolute_path,
            "session_id": session_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "kind": kind,
            "cwd": os.path.abspath(cwd) if cwd else None,
            "title": title,
            "source": "subagent" if kind == "delegated" else None,
            "provider": provider,
            "model": model_id,
            "alias_keys": file_alias_keys("reasonix", absolute_path, session_id),
        }
    )


def _session_paths(config: HistoryConfig, include_delegated: bool) -> list[str]:
    home = config.reasonix_home
    if not home.exists():
        return []
    try:
        with os.scandir(home):
            pass
    except OSError as error:
        raise HistoryStoreError("Reasonix", home, error) from error

    for store_dir in (home / "sessions", home / "projects"):
        if not store_dir.exists():
            continue
        if not store_dir.is_dir():
            warn(store_dir, "expected a directory")
            continue
        try:
            with os.scandir(store_dir):
                pass
        except OSError as error:
            raise HistoryStoreError("Reasonix", store_dir, error) from error

    patterns = [
        home / "sessions" / "*.jsonl",
        home / "projects" / "*" / "sessions" / "*.jsonl",
    ]
    if include_delegated:
        patterns.extend(
            [
                home / "sessions" / "subagents" / "*.jsonl",
                home / "projects" / "*" / "sessions" / "subagents" / "*.jsonl",
            ]
        )
    paths: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in glob.glob(str(pattern)):
            absolute = os.path.abspath(path)
            if absolute in seen:
                continue
            seen.add(absolute)
            paths.append(path)
    return paths
