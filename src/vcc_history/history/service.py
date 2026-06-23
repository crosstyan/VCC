from __future__ import annotations

import os
import re
from pathlib import Path

from . import compiler
from .config import HistoryConfig
from .models import HarnessFilter, SearchHit, SessionRecord
from .refs import absolute_ref
from .sources import SOURCES, selected_sources
from .sources import t3code


def collect_sessions(
    harness: HarnessFilter = "all",
    config: HistoryConfig | None = None,
) -> list[SessionRecord]:
    resolved = config or HistoryConfig.from_env()
    records: list[SessionRecord] = []
    for source in selected_sources(harness):
        records.extend(source.collect(resolved))

    if harness == "all":
        deduped: dict[str, SessionRecord] = {}
        for record in records:
            existing = deduped.get(record["path"])
            if existing is None or record["harness"] == "t3code":
                deduped[record["path"]] = record
        records = list(deduped.values())

    records.sort(key=lambda rec: (rec.get("timestamp") or "", rec["path"]))
    return records


def filter_sessions(
    records: list[SessionRecord],
    cwd: str | None,
    latest: bool,
    limit: int,
    config: HistoryConfig | None = None,
) -> list[SessionRecord]:
    resolved = config or HistoryConfig.from_env()
    filtered = records
    if cwd:
        filtered = [rec for rec in filtered if _matches_cwd(rec, cwd, resolved)]
    if latest:
        return filtered[-1:] if filtered else []
    if limit > 0:
        return filtered[-limit:]
    return filtered


def compile_session(
    path: str,
    grep_pattern: re.Pattern[str] | None = None,
    output_dir: str | None = None,
    config: HistoryConfig | None = None,
) -> list[tuple[str, list[dict[str, object]]]]:
    resolved = config or HistoryConfig.from_env()
    t3_ref = t3code.parse_ref(path)
    if t3_ref is not None:
        db_path, thread_id = t3_ref
        path = str(t3code.export_thread(db_path, thread_id, resolved))

    input_path = Path(path).expanduser()
    if input_path.is_file() and t3code.is_t3code_db(input_path):
        results: list[tuple[str, list[dict[str, object]]]] = []
        for record in t3code.records_from_db(input_path, resolved):
            results.extend(
                compile_session(
                    record["path"],
                    grep_pattern=grep_pattern,
                    output_dir=output_dir,
                    config=resolved,
                )
            )
        return results

    return compiler.compile_pass(
        path,
        output_dir=output_dir,
        grep_pattern=grep_pattern,
        quiet=grep_pattern is not None,
        config=resolved,
    )


def search_sessions(
    records: list[SessionRecord],
    pattern: re.Pattern[str],
    config: HistoryConfig | None = None,
) -> list[SearchHit]:
    resolved = config or HistoryConfig.from_env()
    hits: list[SearchHit] = []
    vcc = compiler.load_compiler(resolved)
    for record in reversed(records):
        for compiled_path, ir in compile_session(record["path"], grep_pattern=pattern, config=resolved):
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
                        "ref": absolute_ref(compiled_path, str(rendered[0])),
                        "lines": [str(line) for line in rendered[1:]],
                    }
                )
    return hits


def _matches_cwd(record: SessionRecord, cwd: str, config: HistoryConfig) -> bool:
    source = SOURCES.get(record["harness"])
    if source is None:
        return False
    return source.matches_cwd(record, cwd, config)
