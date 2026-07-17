from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import compiler, vcc
from .config import HistoryConfig
from .errors import HistoryError
from .models import HarnessFilter, SearchHit, SessionRecord
from .refs import absolute_ref
from .registry import SOURCES, selected_sources
from .sources import t3code
from .sources.base import canonical_path, ensure_private_cache_path, parse_instant


def collect_sessions(
    harness: HarnessFilter = "all",
    config: HistoryConfig | None = None,
    include_delegated: bool = False,
) -> list[SessionRecord]:
    resolved = config or HistoryConfig.from_env()
    records: list[SessionRecord] = []
    for source in selected_sources(harness):
        records.extend(source.collect(resolved, include_delegated=include_delegated))

    if harness == "all":
        records = _deduplicate_records(records)

    records.sort(key=_session_sort_key)
    return records


def _deduplicate_records(records: list[SessionRecord]) -> list[SessionRecord]:
    alias_owners: dict[str, SessionRecord] = {}

    for record in records:
        matches = {
            id(existing): existing
            for key in _alias_keys(record)
            if (existing := alias_owners.get(key)) is not None
        }
        candidates = [*matches.values(), record]
        winner = next(
            (candidate for candidate in reversed(candidates) if candidate["harness"] == "t3code"),
            candidates[0],
        )
        merged_aliases = list(
            dict.fromkeys(key for candidate in candidates for key in _alias_keys(candidate))
        )
        winner["alias_keys"] = merged_aliases
        alias_owners.update(dict.fromkeys(merged_aliases, winner))

    return list({id(record): record for record in alias_owners.values()}.values())


def _alias_keys(record: SessionRecord) -> list[str]:
    return record.get("alias_keys") or [f"path:{canonical_path(record['path'])}"]


def _session_sort_key(record: SessionRecord) -> tuple[bool, datetime, str]:
    instant = parse_instant(record.get("timestamp"))
    return (
        instant is not None,
        instant or datetime.min.replace(tzinfo=timezone.utc),
        record["path"],
    )


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
    all_threads: bool = False,
    harness: str | None = None,
) -> list[tuple[str, list[dict[str, object]]]]:
    resolved = config or HistoryConfig.from_env()
    t3_ref = t3code.parse_ref(path)
    if t3_ref is not None:
        if harness not in {None, "t3code"}:
            raise HistoryError(
                f"T3 Code thread refs require harness 't3code', not {harness!r}"
            )
        db_path, thread_id = t3_ref
        path = str(t3code.export_thread(db_path, thread_id, resolved))
        harness = "t3code"

    input_path = Path(path).expanduser()
    if input_path.is_file() and t3code.is_t3code_db(input_path):
        if not all_threads:
            raise HistoryError(
                "state.sqlite contains many threads; run xcc list --harness t3code and compile "
                "a t3code:<db>#<thread> ref, or pass --all-threads"
            )
        results: list[tuple[str, list[dict[str, object]]]] = []
        for record in t3code.records_from_db(input_path, resolved):
            results.extend(
                compile_session(
                    record["path"],
                    grep_pattern=grep_pattern,
                    output_dir=output_dir,
                    config=resolved,
                    harness=record["harness"],
                )
            )
        return results

    return compiler.compile_pass(
        path,
        output_dir=output_dir,
        grep_pattern=grep_pattern,
        quiet=grep_pattern is not None,
        harness=harness,
    )


def search_sessions(
    records: list[SessionRecord],
    pattern: re.Pattern[str],
    config: HistoryConfig | None = None,
) -> list[SearchHit]:
    resolved = config or HistoryConfig.from_env()
    hits: list[SearchHit] = []
    for record in reversed(records):
        compiled = compile_session(
            record["path"],
            grep_pattern=pattern,
            output_dir=str(_search_cache_dir(record, resolved)),
            config=resolved,
            harness=record["harness"],
        )
        for compiled_path, ir in reversed(compiled):
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


def _search_cache_dir(record: SessionRecord, config: HistoryConfig) -> Path:
    source_ref = _canonical_source_ref(record["path"])
    cache_key = f"{record['harness']}\0{source_ref}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    cache_root = Path(canonical_path(config.cache_dir))
    result = cache_root / "compiled" / record["harness"] / digest
    ensure_private_cache_path(cache_root, result)
    return result


def _canonical_source_ref(value: str) -> str:
    t3_ref = t3code.parse_ref(value)
    if t3_ref is not None:
        db_path, thread_id = t3_ref
        return f"t3code\0{canonical_path(db_path)}\0{thread_id}"
    return canonical_path(value)


def _matches_cwd(record: SessionRecord, cwd: str, config: HistoryConfig) -> bool:
    source = SOURCES.get(record["harness"])
    if source is None:
        return False
    return source.matches_cwd(record, cwd, config)
