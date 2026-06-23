from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from ..config import HistoryConfig
from ..errors import HistoryError
from ..json_utils import json_or_none, nested_get, string_or_none
from ..models import SessionRecord
from . import codex
from .base import ExactCwdMixin

_LOG_LINE_RE = re.compile(r"^\[[^\]]+\]\s+(?:NTIVE|CANON):\s+(.+)$")


class T3CodeSource(ExactCwdMixin):
    harness = "t3code"

    def collect(self, config: HistoryConfig) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        for db_path in db_paths(config):
            records.extend(records_from_db(db_path, config))
        return records


def db_paths(config: HistoryConfig) -> list[Path]:
    candidates: list[Path] = []
    if config.t3code_db_override:
        candidates.extend(Path(item).expanduser() for item in config.t3code_db_override.split(os.pathsep) if item)

    base = config.t3code_home
    candidates.extend(
        [
            base / "userdata" / "state.sqlite",
            base / "dev" / "state.sqlite",
            base / "state.sqlite",
        ]
    )

    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            resolved = candidate.expanduser()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        if is_t3code_db(resolved):
            result.append(resolved)
    return result


def is_t3code_db(path: Path) -> bool:
    tables = _sqlite_tables(path)
    return {
        "projection_projects",
        "projection_threads",
        "projection_thread_messages",
        "projection_thread_activities",
    }.issubset(tables)


def parse_ref(value: str) -> tuple[Path, str] | None:
    if not value.startswith("t3code:") or "#" not in value:
        return None
    path_text, thread_text = value[len("t3code:") :].split("#", 1)
    if not path_text or not thread_text:
        return None
    return Path(unquote(path_text)).expanduser(), unquote(thread_text)


def export_thread(db_path: Path, thread_id: str, config: HistoryConfig) -> Path:
    out_path = _export_path(db_path, thread_id, config)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(_sqlite_ro(db_path)) as conn:
        thread = conn.execute(
            """
            SELECT
              t.thread_id,
              t.title,
              t.created_at,
              t.updated_at,
              t.deleted_at,
              t.model_selection_json,
              p.workspace_root,
              s.provider_name,
              s.provider_instance_id
            FROM projection_threads t
            LEFT JOIN projection_projects p ON p.project_id = t.project_id
            LEFT JOIN projection_thread_sessions s ON s.thread_id = t.thread_id
            WHERE t.thread_id = ?
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
        if thread is None:
            raise HistoryError(f"T3 Code thread not found in {db_path}: {thread_id}")

        messages = conn.execute(
            """
            SELECT
              message_id AS id,
              thread_id,
              turn_id,
              role,
              text,
              attachments_json,
              created_at,
              updated_at
            FROM projection_thread_messages
            WHERE thread_id = ?
            ORDER BY created_at ASC, message_id ASC
            """,
            (thread_id,),
        ).fetchall()

        activities = conn.execute(
            """
            SELECT
              activity_id AS id,
              thread_id,
              turn_id,
              tone,
              kind,
              summary,
              payload_json,
              created_at,
              sequence
            FROM projection_thread_activities
            WHERE thread_id = ?
            ORDER BY
              CASE WHEN sequence IS NULL THEN 0 ELSE 1 END ASC,
              sequence ASC,
              created_at ASC,
              activity_id ASC
            """,
            (thread_id,),
        ).fetchall()

    provider, model = _model_parts(thread["model_selection_json"], thread["provider_name"])
    entries: list[dict[str, Any]] = [
        {
            "type": "t3_session",
            "id": thread_id,
            "timestamp": thread["created_at"],
            "cwd": thread["workspace_root"],
            "title": thread["title"],
            "model": _model_label(provider, model),
        },
    ]

    sortable: list[tuple[str, int, str, dict[str, Any]]] = []
    for row in messages:
        role = row["role"]
        if role in {"user", "assistant", "developer"}:
            record = {
                "type": "t3_message",
                "timestamp": row["created_at"],
                "id": row["id"],
                "turn_id": row["turn_id"],
                "role": role,
                "content": _message_content(row["text"], row["attachments_json"]),
            }
        else:
            record = {
                "type": "t3_custom_message",
                "timestamp": row["created_at"],
                "customType": f"message:{role}",
                "content": _message_content(row["text"], row["attachments_json"]),
            }
        sortable.append((str(row["created_at"] or ""), 0, str(row["id"]), record))

    for row in activities:
        payload = json_or_none(row["payload_json"])
        details: dict[str, Any] = {
            "tone": row["tone"],
            "turnId": row["turn_id"],
        }
        if payload is not None:
            details["payload"] = payload
        record = {
            "type": "t3_activity",
            "timestamp": row["created_at"],
            "customType": f"activity:{row['kind']}",
            "content": row["summary"],
            "details": details,
        }
        sequence = row["sequence"] if isinstance(row["sequence"], int) else 0
        sortable.append((str(row["created_at"] or ""), 1 + sequence, str(row["id"]), record))

    entries.extend(record for _, _, _, record in sorted(sortable))
    with open(out_path, "w", encoding="utf-8") as fh:
        for entry in entries:
            json.dump(entry, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write("\n")
    return out_path


def records_from_db(db_path: Path, config: HistoryConfig) -> list[SessionRecord]:
    try:
        with closing(_sqlite_ro(db_path)) as conn:
            rows = conn.execute(
                """
                SELECT
                  t.thread_id,
                  t.title,
                  t.created_at,
                  t.updated_at,
                  t.deleted_at,
                  t.model_selection_json,
                  p.workspace_root,
                  s.provider_name,
                  s.provider_thread_id,
                  s.provider_instance_id
                FROM projection_threads t
                LEFT JOIN projection_projects p ON p.project_id = t.project_id
                LEFT JOIN projection_thread_sessions s ON s.thread_id = t.thread_id
                WHERE t.deleted_at IS NULL
                ORDER BY t.updated_at ASC, t.thread_id ASC
                """
            ).fetchall()
    except sqlite3.Error as error:
        raise HistoryError(f"Could not read T3 Code history DB {db_path}: {error}") from error

    records: list[SessionRecord] = []
    for row in rows:
        thread_id = str(row["thread_id"])
        source_path = _codex_rollout_path(db_path, thread_id, config)
        if source_path is None:
            source_path = codex.rollout_for_session_id(string_or_none(row["provider_thread_id"]), config)
        path_text = str(source_path) if source_path is not None else _ref(db_path, thread_id)
        provider, model = _model_parts(row["model_selection_json"], row["provider_name"])
        records.append(
            {
                "harness": "t3code",
                "path": path_text,
                "session_id": thread_id,
                "timestamp": string_or_none(row["updated_at"] or row["created_at"]),
                "cwd": string_or_none(row["workspace_root"]),
                "title": string_or_none(row["title"]),
                "source": str(db_path),
                "provider": provider,
                "model": model,
            }
        )
    return records


def _sqlite_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path), safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 1000")
    return conn


def _sqlite_tables(path: Path) -> set[str]:
    try:
        with closing(_sqlite_ro(path)) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row["name"]) for row in rows}


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned or "thread"


def _export_path(db_path: Path, thread_id: str, config: HistoryConfig) -> Path:
    digest = hashlib.sha256(str(db_path).encode("utf-8")).hexdigest()[:16]
    return config.cache_dir / "t3code" / digest / f"{_safe_filename(thread_id)}.jsonl"


def _ref(db_path: Path, thread_id: str) -> str:
    return f"t3code:{quote(str(db_path), safe='/:')}#{quote(thread_id, safe='')}"


def _model_parts(model_selection_json: object, provider_name: object) -> tuple[str | None, str | None]:
    parsed = json_or_none(model_selection_json)
    provider = string_or_none(provider_name)
    model = None
    if isinstance(parsed, dict):
        provider = string_or_none(parsed.get("provider") or parsed.get("instanceId")) or provider
        model = string_or_none(parsed.get("model"))
    return provider, model


def _model_label(provider: str | None, model: str | None) -> str | None:
    if provider and model:
        return f"{provider}/{model}"
    return model or provider


def _provider_log_paths(db_path: Path, thread_id: str) -> list[Path]:
    logs_dir = db_path.parent / "logs" / "provider"
    if not logs_dir.is_dir():
        return []
    paths = [path for path in logs_dir.glob(f"{thread_id}.log*") if path.is_file()]
    return sorted(paths, key=lambda path: (path.stat().st_mtime, str(path)))


def _iter_log_events(db_path: Path, thread_id: str) -> Iterator[dict[str, Any]]:
    for log_path in _provider_log_paths(db_path, thread_id):
        try:
            with open(log_path, encoding="utf-8") as fh:
                for line in fh:
                    match = _LOG_LINE_RE.match(line)
                    if not match:
                        continue
                    value = json.loads(match.group(1))
                    if isinstance(value, dict):
                        yield value
        except (OSError, ValueError, TypeError):
            continue


def _codex_rollout_path(db_path: Path, thread_id: str, config: HistoryConfig) -> Path | None:
    for event in _iter_log_events(db_path, thread_id):
        if event.get("provider") != "codex":
            continue

        raw_path = nested_get(event, ("payload", "thread", "path"))
        if raw_path is None:
            raw_path = nested_get(event, ("raw", "payload", "thread", "path"))
        if isinstance(raw_path, str):
            path = Path(raw_path).expanduser()
            if path.is_file() and codex.record_from_path(str(path)):
                return path.resolve()

        provider_thread_id = string_or_none(nested_get(event, ("payload", "providerThreadId")))
        if provider_thread_id is None:
            provider_thread_id = string_or_none(nested_get(event, ("payload", "thread", "id")))
        if provider_thread_id is None:
            provider_thread_id = string_or_none(nested_get(event, ("raw", "payload", "thread", "id")))
        path = codex.rollout_for_session_id(provider_thread_id, config)
        if path:
            return path
    return None


def _message_content(text: str, attachments_json: object) -> str:
    attachments = json_or_none(attachments_json)
    if not attachments:
        return text
    rendered = json.dumps(attachments, ensure_ascii=False, indent=2)
    if text:
        return f"{text}\n\nattachments:\n{rendered}"
    return f"attachments:\n{rendered}"
