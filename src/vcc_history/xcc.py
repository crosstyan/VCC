from __future__ import annotations

import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, TypedDict, cast
from urllib.parse import quote, unquote

import click

Harness = Literal["omp", "codex", "t3code"]
HarnessFilter = Literal["all", "omp", "codex", "t3code"]


class SessionRecord(TypedDict, total=False):
    harness: Harness
    path: str
    session_id: str | None
    timestamp: str | None
    cwd: str | None
    title: str | None
    source: str | None
    provider: str | None
    model: str | None


class SearchHit(TypedDict):
    harness: Harness
    session_path: str
    compiled_path: str
    block_type: str
    ref: str
    lines: list[str]


_VCC_MODULE: ModuleType | None = None
_T3_LOG_LINE_RE = re.compile(r"^\[[^\]]+\]\s+(?:NTIVE|CANON):\s+(.+)$")


def _repo_root() -> Path:
    override = os.environ.get("VCC_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _compiler_path() -> Path:
    override = os.environ.get("VCC_COMPILER_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return _repo_root() / "skills" / "conversation-compiler" / "scripts" / "VCC.py"


def _vcc() -> ModuleType:
    global _VCC_MODULE
    if _VCC_MODULE is not None:
        return _VCC_MODULE
    path = _compiler_path()
    spec = importlib.util.spec_from_file_location("vcc_legacy_compiler", path)
    if spec is None or spec.loader is None:
        raise click.ClickException(f"Could not load VCC compiler from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _VCC_MODULE = module
    return module


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def _omp_agent_dir() -> Path:
    return Path(os.environ.get("PI_CODING_AGENT_DIR", "~/.omp/agent")).expanduser()


def _t3code_base_dir() -> Path:
    return Path(os.environ.get("T3CODE_HOME", "~/.t3")).expanduser()


def _cache_dir() -> Path:
    return Path(os.environ.get("VCC_CACHE_DIR", "~/.cache/vcc-history")).expanduser()


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _json_or_none(value: object) -> object | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _read_first_json(path: str) -> dict[str, object] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                value = json.loads(line)
                return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None
    return None


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
                return _string_or_none(value.get("model"))
    except (OSError, ValueError, TypeError):
        return None
    return None


def _codex_record(path: str) -> SessionRecord | None:
    obj = _read_first_json(path)
    if not obj or obj.get("type") != "session_meta":
        return None
    payload_obj = obj.get("payload")
    payload = payload_obj if isinstance(payload_obj, dict) else {}
    return {
        "harness": "codex",
        "path": os.path.abspath(path),
        "session_id": _string_or_none(payload.get("id")),
        "timestamp": _string_or_none(payload.get("timestamp") or obj.get("timestamp")),
        "cwd": _string_or_none(payload.get("cwd")),
        "title": None,
        "source": _string_or_none(payload.get("source")),
        "provider": None,
        "model": None,
    }


def _omp_record(path: str) -> SessionRecord | None:
    obj = _read_first_json(path)
    if not obj or obj.get("type") != "session":
        return None
    model = _read_model_change(path) or _string_or_none(obj.get("modelId") or obj.get("model"))
    provider = None
    model_id = model
    if model and "/" in model:
        provider, model_id = model.split("/", 1)
    return {
        "harness": "omp",
        "path": os.path.abspath(path),
        "session_id": _string_or_none(obj.get("id")),
        "timestamp": _string_or_none(obj.get("timestamp")),
        "cwd": _string_or_none(obj.get("cwd")),
        "title": _string_or_none(obj.get("title")),
        "source": None,
        "provider": provider,
        "model": model_id,
    }


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


def _is_t3code_db(path: Path) -> bool:
    tables = _sqlite_tables(path)
    return {
        "projection_projects",
        "projection_threads",
        "projection_thread_messages",
        "projection_thread_activities",
    }.issubset(tables)


def _t3code_db_paths() -> list[Path]:
    candidates: list[Path] = []
    override = os.environ.get("VCC_T3CODE_DB")
    if override:
        candidates.extend(Path(item).expanduser() for item in override.split(os.pathsep) if item)

    base = _t3code_base_dir()
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
        if _is_t3code_db(resolved):
            result.append(resolved)
    return result


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned or "thread"


def _t3code_export_path(db_path: Path, thread_id: str) -> Path:
    digest = hashlib.sha256(str(db_path).encode("utf-8")).hexdigest()[:16]
    return _cache_dir() / "t3code" / digest / f"{_safe_filename(thread_id)}.jsonl"


def _t3code_ref(db_path: Path, thread_id: str) -> str:
    return f"t3code:{quote(str(db_path), safe='/:')}#{quote(thread_id, safe='')}"


def _parse_t3code_ref(value: str) -> tuple[Path, str] | None:
    if not value.startswith("t3code:") or "#" not in value:
        return None
    path_text, thread_text = value[len("t3code:") :].split("#", 1)
    if not path_text or not thread_text:
        return None
    return Path(unquote(path_text)).expanduser(), unquote(thread_text)


def _t3code_model_parts(model_selection_json: object, provider_name: object) -> tuple[str | None, str | None]:
    parsed = _json_or_none(model_selection_json)
    provider = _string_or_none(provider_name)
    model = None
    if isinstance(parsed, dict):
        provider = _string_or_none(parsed.get("provider") or parsed.get("instanceId")) or provider
        model = _string_or_none(parsed.get("model"))
    return provider, model


def _t3code_model_label(provider: str | None, model: str | None) -> str | None:
    if provider and model:
        return f"{provider}/{model}"
    return model or provider


def _nested_get(value: object, keys: tuple[str, ...]) -> object | None:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _t3code_provider_log_paths(db_path: Path, thread_id: str) -> list[Path]:
    logs_dir = db_path.parent / "logs" / "provider"
    if not logs_dir.is_dir():
        return []
    paths = [path for path in logs_dir.glob(f"{thread_id}.log*") if path.is_file()]
    return sorted(paths, key=lambda path: (path.stat().st_mtime, str(path)))


def _iter_t3code_log_events(db_path: Path, thread_id: str) -> Iterator[dict[str, Any]]:
    for log_path in _t3code_provider_log_paths(db_path, thread_id):
        try:
            with open(log_path, encoding="utf-8") as fh:
                for line in fh:
                    match = _T3_LOG_LINE_RE.match(line)
                    if not match:
                        continue
                    value = json.loads(match.group(1))
                    if isinstance(value, dict):
                        yield value
        except (OSError, ValueError, TypeError):
            continue


def _codex_rollout_for_session_id(session_id: str | None) -> Path | None:
    if not session_id:
        return None
    pattern = str(_codex_home() / "sessions" / "**" / f"*{session_id}*.jsonl")
    matches = sorted(glob.glob(pattern, recursive=True))
    for match in matches:
        path = Path(match)
        if path.is_file() and _codex_record(str(path)):
            return path.resolve()
    return None


def _t3code_codex_rollout_path(db_path: Path, thread_id: str) -> Path | None:
    for event in _iter_t3code_log_events(db_path, thread_id):
        if event.get("provider") != "codex":
            continue

        raw_path = _nested_get(event, ("payload", "thread", "path"))
        if raw_path is None:
            raw_path = _nested_get(event, ("raw", "payload", "thread", "path"))
        if isinstance(raw_path, str):
            path = Path(raw_path).expanduser()
            if path.is_file() and _codex_record(str(path)):
                return path.resolve()

        provider_thread_id = _string_or_none(_nested_get(event, ("payload", "providerThreadId")))
        if provider_thread_id is None:
            provider_thread_id = _string_or_none(_nested_get(event, ("payload", "thread", "id")))
        if provider_thread_id is None:
            provider_thread_id = _string_or_none(_nested_get(event, ("raw", "payload", "thread", "id")))
        path = _codex_rollout_for_session_id(provider_thread_id)
        if path:
            return path
    return None


def _t3code_message_content(text: str, attachments_json: object) -> str:
    attachments = _json_or_none(attachments_json)
    if not attachments:
        return text
    rendered = json.dumps(attachments, ensure_ascii=False, indent=2)
    if text:
        return f"{text}\n\nattachments:\n{rendered}"
    return f"attachments:\n{rendered}"


def _t3code_export_thread(db_path: Path, thread_id: str) -> Path:
    out_path = _t3code_export_path(db_path, thread_id)
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
            raise click.ClickException(f"T3 Code thread not found in {db_path}: {thread_id}")

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

    provider, model = _t3code_model_parts(thread["model_selection_json"], thread["provider_name"])
    entries: list[dict[str, Any]] = [
        {
            "type": "t3_session",
            "id": thread_id,
            "timestamp": thread["created_at"],
            "cwd": thread["workspace_root"],
            "title": thread["title"],
            "model": _t3code_model_label(provider, model),
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
                "content": _t3code_message_content(row["text"], row["attachments_json"]),
            }
        else:
            record = {
                "type": "t3_custom_message",
                "timestamp": row["created_at"],
                "customType": f"message:{role}",
                "content": _t3code_message_content(row["text"], row["attachments_json"]),
            }
        sortable.append((str(row["created_at"] or ""), 0, str(row["id"]), record))

    for row in activities:
        payload = _json_or_none(row["payload_json"])
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


def _t3code_records_from_db(db_path: Path) -> list[SessionRecord]:
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
        raise click.ClickException(f"Could not read T3 Code history DB {db_path}: {error}") from error

    records: list[SessionRecord] = []
    for row in rows:
        thread_id = str(row["thread_id"])
        source_path = _t3code_codex_rollout_path(db_path, thread_id)
        if source_path is None:
            source_path = _codex_rollout_for_session_id(_string_or_none(row["provider_thread_id"]))
        path_text = str(source_path) if source_path is not None else _t3code_ref(db_path, thread_id)
        provider, model = _t3code_model_parts(row["model_selection_json"], row["provider_name"])
        records.append(
            {
                "harness": "t3code",
                "path": path_text,
                "session_id": thread_id,
                "timestamp": _string_or_none(row["updated_at"] or row["created_at"]),
                "cwd": _string_or_none(row["workspace_root"]),
                "title": _string_or_none(row["title"]),
                "source": str(db_path),
                "provider": provider,
                "model": model,
            }
        )
    return records


def collect_sessions(harness: HarnessFilter = "all") -> list[SessionRecord]:
    records: list[SessionRecord] = []
    if harness in {"all", "codex"}:
        sessions_dir = _codex_home() / "sessions"
        if sessions_dir.is_dir():
            pattern = str(sessions_dir / "**" / "*.jsonl")
            for path in glob.glob(pattern, recursive=True):
                record = _codex_record(path)
                if record:
                    records.append(record)
    if harness in {"all", "omp"}:
        sessions_dir = _omp_agent_dir() / "sessions"
        if sessions_dir.is_dir():
            pattern = str(sessions_dir / "**" / "*.jsonl")
            for path in glob.glob(pattern, recursive=True):
                record = _omp_record(path)
                if record:
                    records.append(record)
    if harness in {"all", "t3code"}:
        for db_path in _t3code_db_paths():
            records.extend(_t3code_records_from_db(db_path))
    if harness == "all":
        deduped: dict[str, SessionRecord] = {}
        for record in records:
            existing = deduped.get(record["path"])
            if existing is None or record["harness"] == "t3code":
                deduped[record["path"]] = record
        records = list(deduped.values())
    records.sort(key=lambda rec: (rec.get("timestamp") or "", rec["path"]))
    return records


def filter_sessions(records: list[SessionRecord], cwd: str | None, latest: bool, limit: int) -> list[SessionRecord]:
    filtered = records
    if cwd:
        target = os.path.abspath(cwd)
        filtered = [rec for rec in filtered if rec.get("cwd") == target]
    if latest:
        return filtered[-1:] if filtered else []
    if limit > 0:
        return filtered[-limit:]
    return filtered


def compile_session(
    path: str,
    grep_pattern: re.Pattern[str] | None = None,
    output_dir: str | None = None,
) -> list[tuple[str, list[dict[str, object]]]]:
    t3_ref = _parse_t3code_ref(path)
    if t3_ref is not None:
        db_path, thread_id = t3_ref
        path = str(_t3code_export_thread(db_path, thread_id))
    input_path = Path(path).expanduser()
    if input_path.is_file() and _is_t3code_db(input_path):
        results: list[tuple[str, list[dict[str, object]]]] = []
        for record in _t3code_records_from_db(input_path):
            results.extend(compile_session(record["path"], grep_pattern=grep_pattern, output_dir=output_dir))
        return results
    result = _vcc().compile_pass(path, output_dir=output_dir, grep_pattern=grep_pattern, quiet=grep_pattern is not None)
    return cast(list[tuple[str, list[dict[str, object]]]], result)


def search_sessions(records: list[SessionRecord], pattern: re.Pattern[str]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    vcc = _vcc()
    for record in reversed(records):
        for compiled_path, ir in compile_session(record["path"], grep_pattern=pattern):
            for node in reversed(ir):
                if not node.get("searchable"):
                    continue
                content = node.get("content")
                if not isinstance(content, list):
                    continue
                lines = [str(item) for item in content]
                rendered = vcc.match_lines(
                    lines,
                    pattern,
                    os.path.basename(compiled_path),
                    int(node.get("start_line", 0)) + 1,
                )
                if len(rendered) <= 1:
                    continue
                hits.append(
                    {
                        "harness": record["harness"],
                        "session_path": record["path"],
                        "compiled_path": compiled_path,
                        "block_type": str(node.get("type", "unknown")),
                        "ref": _absolute_ref(compiled_path, str(rendered[0])),
                        "lines": [str(line) for line in rendered[1:]],
                    }
                )
    return hits


def _absolute_ref(compiled_path: str, block_ref: str) -> str:
    match = re.search(r":(\d+-\d+)\)$", block_ref)
    if not match:
        return compiled_path
    return f"{compiled_path}:{match.group(1)}"


def read_ref(ref: str) -> str:
    path_text, ranges = _split_ref(ref)
    text = Path(path_text).read_text(encoding="utf-8")
    if ranges is None:
        return text
    lines = text.splitlines()
    selected: list[str] = []
    for start, end in ranges:
        lo = max(1, start)
        hi = min(len(lines), end)
        if hi < lo:
            continue
        selected.extend(lines[lo - 1 : hi])
    return "\n".join(selected) + ("\n" if selected else "")


def _split_ref(ref: str) -> tuple[str, list[tuple[int, int]] | None]:
    match = re.match(r"^(.+):([0-9][0-9,+-]*)$", ref)
    if not match:
        return ref, None
    ranges: list[tuple[int, int]] = []
    for chunk in match.group(2).split(","):
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            ranges.append((int(start_text), int(end_text)))
        elif "+" in chunk:
            start_text, count_text = chunk.split("+", 1)
            start = int(start_text)
            ranges.append((start, start + int(count_text) - 1))
        else:
            start = int(chunk)
            ranges.append((start, start))
    return match.group(1), ranges


def _print_records(records: list[SessionRecord], as_json: bool) -> None:
    if as_json:
        json.dump(records, sys.stdout, ensure_ascii=False, indent=2)
        if records:
            sys.stdout.write("\n")
        return
    for rec in records:
        cwd = f" cwd={rec['cwd']}" if rec.get("cwd") else ""
        title = f" title={rec['title']}" if rec.get("title") else ""
        print(f"{rec['harness']}\t{rec.get('timestamp') or '-'}\t{rec['path']}{cwd}{title}")


def _print_hits(hits: list[SearchHit], as_json: bool) -> None:
    if as_json:
        json.dump(hits, sys.stdout, ensure_ascii=False, indent=2)
        if hits:
            sys.stdout.write("\n")
        return
    first = True
    for hit in hits:
        if not first:
            print()
        first = False
        print(f"{hit['harness']} {hit['session_path']}")
        print(f"{hit['ref']} [{hit['block_type']}]")
        for line in hit["lines"]:
            print(line)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Cross-harness VCC history CLI."""


@main.command("list")
@click.option("--harness", type=click.Choice(["all", "omp", "codex", "t3code"]), default="all", show_default=True)
@click.option("--cwd", type=click.Path(path_type=str), help="Filter by exact session cwd.")
@click.option("--latest", is_flag=True, help="Return only the newest matching session.")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def list_cmd(harness: HarnessFilter, cwd: str | None, latest: bool, limit: int, as_json: bool) -> None:
    """List Codex, OMP, and T3 Code session files."""
    records = filter_sessions(collect_sessions(harness), cwd, latest, limit)
    _print_records(records, as_json)


@main.command()
@click.argument("pattern")
@click.option("--harness", type=click.Choice(["all", "omp", "codex", "t3code"]), default="all", show_default=True)
@click.option("--cwd", type=click.Path(path_type=str), help="Filter by exact session cwd.")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--ignore-case", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def search(pattern: str, harness: HarnessFilter, cwd: str | None, limit: int, ignore_case: bool, as_json: bool) -> None:
    """Search recent Codex, OMP, and T3 Code sessions."""
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as error:
        raise click.BadParameter(f"invalid regex: {error}", param_hint="pattern") from error
    records = filter_sessions(collect_sessions(harness), cwd, False, limit)
    _print_hits(search_sessions(records, compiled), as_json)


@main.command("compile")
@click.argument("inputs", nargs=-1, required=True, type=click.Path(path_type=str))
@click.option("-o", "--output-dir", type=click.Path(path_type=str))
def compile_cmd(inputs: tuple[str, ...], output_dir: str | None) -> None:
    """Compile one or more session files."""
    for input_path in inputs:
        compile_session(input_path, output_dir=output_dir)


@main.command("read")
@click.argument("ref")
def read_cmd(ref: str) -> None:
    """Read a compiled transcript ref such as path.txt:10-30."""
    sys.stdout.write(read_ref(ref))


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
