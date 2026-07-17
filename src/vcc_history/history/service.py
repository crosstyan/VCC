from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import compiler, vcc
from .config import HistoryConfig
from .errors import HistoryError, HistoryStoreError, warn
from .models import HarnessFilter, SearchMatch, SessionRecord
from .registry import SOURCES, selected_sources
from .sources import t3code
from .sources.base import (
    canonical_path,
    ensure_private_cache_path,
    exclusive_cache_lock,
    parse_instant,
)

_FINGERPRINT_FILE = ".fingerprint"
_NODES_FILE = ".nodes.json"
_LOCK_FILE = ".lock"


def collect_sessions(
    harness: HarnessFilter = "all",
    config: HistoryConfig | None = None,
    include_delegated: bool = False,
) -> list[SessionRecord]:
    resolved = config or HistoryConfig.from_env()
    records: list[SessionRecord] = []
    for source in selected_sources(harness):
        try:
            records.extend(source.collect(resolved, include_delegated=include_delegated))
        except HistoryStoreError as error:
            if harness != "all":
                raise
            warn(error.path, f"{error.harness}: {error.reason}")

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
    output_dir: str | None = None,
    config: HistoryConfig | None = None,
    all_threads: bool = False,
    harness: str | None = None,
    quiet: bool = False,
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
    if (
        input_path.is_file()
        and input_path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
        and t3code.is_t3code_db(input_path)
    ):
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
                    output_dir=output_dir,
                    config=resolved,
                    harness=record["harness"],
                    quiet=quiet,
                )
            )
        return results

    return compiler.compile_pass(
        path,
        output_dir=output_dir,
        quiet=quiet,
        harness=harness,
    )


def search_sessions(
    records: list[SessionRecord],
    pattern: re.Pattern[str],
    config: HistoryConfig | None = None,
) -> list[SearchMatch]:
    resolved = config or HistoryConfig.from_env()
    hits: list[SearchMatch] = []
    for record in reversed(records):
        compiled = _compiled_search_session(record, resolved)
        for compiled_path, ir in reversed(compiled):
            for node in reversed(ir):
                if not node.get("searchable"):
                    continue
                content = node.get("content")
                if not isinstance(content, list):
                    continue
                node_start = node.get("start_line")
                node_end = node.get("end_line")
                if not isinstance(node_start, int) or not isinstance(node_end, int):
                    continue
                start_line = node_start + 1
                end_line = node_end + 1
                matched_lines = [
                    vcc.format_search_line(start_line + offset, str(line))
                    for offset, line in enumerate(content)
                    if pattern.search(str(line))
                ]
                if not matched_lines:
                    continue
                hits.append(
                    {
                        "harness": record["harness"],
                        "session_path": record["path"],
                        "compiled_path": compiled_path,
                        "block_type": str(node.get("type", "unknown")),
                        "ref": f"{compiled_path}:{start_line}-{end_line}",
                        "lines": matched_lines,
                    }
                )
    return hits


def _compiled_search_session(
    record: SessionRecord, config: HistoryConfig
) -> list[tuple[str, list[dict[str, object]]]]:
    cache_dir = _search_cache_dir(record, config)
    lock_path = cache_dir / _LOCK_FILE
    with exclusive_cache_lock(lock_path):
        fingerprint = _record_fingerprint(record)
        cached = _load_cached_nodes(cache_dir, fingerprint)
        if cached is not None:
            return cached

        temp_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{cache_dir.name}.tmp-{os.getpid()}-",
                dir=cache_dir.parent,
            )
        )
        try:
            compiled = compile_session(
                record["path"],
                output_dir=str(temp_dir),
                config=config,
                harness=record["harness"],
                quiet=True,
            )
            current_fingerprint = _record_fingerprint(record)
            if current_fingerprint != fingerprint:
                shutil.rmtree(temp_dir)
                temp_dir = Path(
                    tempfile.mkdtemp(
                        prefix=f".{cache_dir.name}.tmp-{os.getpid()}-",
                        dir=cache_dir.parent,
                    )
                )
                compiled = compile_session(
                    record["path"],
                    output_dir=str(temp_dir),
                    config=config,
                    harness=record["harness"],
                    quiet=True,
                )
                fingerprint = _record_fingerprint(record)
            return _publish_compiled_cache(cache_dir, temp_dir, compiled, fingerprint)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _load_cached_nodes(
    cache_dir: Path, fingerprint: str
) -> list[tuple[str, list[dict[str, object]]]] | None:
    fingerprint_path = cache_dir / _FINGERPRINT_FILE
    nodes_path = cache_dir / _NODES_FILE
    if any(path.is_symlink() for path in (fingerprint_path, nodes_path)):
        return None
    try:
        if fingerprint_path.read_text(encoding="utf-8") != fingerprint:
            return None
        index = json.loads(nodes_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(index, dict):
        return None

    files = index.get("files")
    outputs = index.get("outputs")
    if not isinstance(files, list) or not isinstance(outputs, list):
        return None
    for name in files:
        path = _safe_cache_member(cache_dir, name)
        if path is None or path.is_symlink() or not path.is_file():
            return None

    compiled: list[tuple[str, list[dict[str, object]]]] = []
    for output in outputs:
        if not isinstance(output, dict):
            return None
        path = _safe_cache_member(cache_dir, output.get("path"))
        nodes = output.get("nodes")
        if path is None or path.is_symlink() or not path.is_file() or not isinstance(nodes, list):
            return None
        if not all(isinstance(node, dict) for node in nodes):
            return None
        compiled.append((str(path), nodes))
    return compiled


def _publish_compiled_cache(
    cache_dir: Path,
    temp_dir: Path,
    compiled: list[tuple[str, list[dict[str, object]]]],
    fingerprint: str,
) -> list[tuple[str, list[dict[str, object]]]]:
    outputs: list[dict[str, object]] = []
    published: list[tuple[str, list[dict[str, object]]]] = []
    for compiled_path, ir in compiled:
        try:
            relative = Path(compiled_path).resolve().relative_to(temp_dir.resolve())
        except ValueError as error:
            raise HistoryError(
                f"Compiler wrote outside its cache staging directory: {compiled_path}"
            ) from error
        indexed_nodes = [_search_node(node) for node in ir if node.get("searchable")]
        outputs.append({"path": relative.as_posix(), "nodes": indexed_nodes})
        published.append((str(cache_dir / relative), indexed_nodes))

    data_files = sorted(
        path.relative_to(temp_dir).as_posix()
        for path in temp_dir.rglob("*")
        if path.is_file()
    )
    index = {"files": data_files, "outputs": outputs}
    nodes_path = temp_dir / _NODES_FILE
    fingerprint_path = temp_dir / _FINGERPRINT_FILE
    nodes_path.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    fingerprint_path.write_text(fingerprint, encoding="utf-8")
    nodes_path.chmod(0o600)
    fingerprint_path.chmod(0o600)

    publish_order = [
        *(temp_dir / name for name in data_files),
        nodes_path,
        fingerprint_path,
    ]
    current_names = {path.relative_to(temp_dir).as_posix() for path in publish_order}
    for source in publish_order:
        relative = source.relative_to(temp_dir)
        target = cache_dir / relative
        ensure_private_cache_path(cache_dir, target.parent)
        os.replace(source, target)

    stale_paths = sorted(cache_dir.rglob("*"), key=lambda path: len(path.parts), reverse=True)
    for stale in stale_paths:
        relative_name = stale.relative_to(cache_dir).as_posix()
        if relative_name == _LOCK_FILE or relative_name in current_names:
            continue
        if stale.is_dir() and not stale.is_symlink():
            try:
                stale.rmdir()
            except OSError:
                pass
        else:
            stale.unlink(missing_ok=True)
    return published


def _search_node(node: dict[str, object]) -> dict[str, object]:
    return {
        "type": str(node.get("type", "unknown")),
        "searchable": True,
        "start_line": node.get("start_line"),
        "end_line": node.get("end_line"),
        "content": node.get("content"),
    }


def _safe_cache_member(cache_dir: Path, value: object) -> Path | None:
    if not isinstance(value, str):
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return cache_dir / relative


def _record_fingerprint(record: SessionRecord) -> str:
    source_path = _record_source_path(record)
    try:
        status = source_path.stat()
    except OSError as error:
        raise HistoryError(f"Could not fingerprint session source {source_path}: {error}") from error
    return json.dumps(
        {
            "compiler_version": vcc.COMPILER_VERSION,
            "mtime_ns": status.st_mtime_ns,
            "size": status.st_size,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _record_source_path(record: SessionRecord) -> Path:
    t3_ref = t3code.parse_ref(record["path"])
    if t3_ref is not None:
        return Path(canonical_path(t3_ref[0]))
    return Path(canonical_path(record["path"]))


def _search_cache_dir(record: SessionRecord, config: HistoryConfig) -> Path:
    result = _search_cache_path(record, config)
    cache_root = Path(canonical_path(config.cache_dir))
    ensure_private_cache_path(cache_root, result)
    return result


def _search_cache_path(record: SessionRecord, config: HistoryConfig) -> Path:
    source_ref = _canonical_source_ref(record["path"])
    cache_key = f"{record['harness']}\0{source_ref}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    cache_root = Path(canonical_path(config.cache_dir))
    return cache_root / "compiled" / record["harness"] / digest


def prune_cache(
    max_age_days: float | None = None,
    config: HistoryConfig | None = None,
) -> list[Path]:
    if max_age_days is not None and max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")
    resolved = config or HistoryConfig.from_env()
    cache_root = Path(canonical_path(resolved.cache_dir))
    if not cache_root.exists():
        return []
    ensure_private_cache_path(cache_root, cache_root)

    records: list[SessionRecord] = []
    live: dict[Path, str] = {}
    unavailable_harnesses: set[str] = set()
    for source in selected_sources("all"):
        try:
            source_records = source.collect(resolved, include_delegated=True)
        except HistoryStoreError as error:
            warn(error.path, f"{error.harness}: {error.reason}")
            unavailable_harnesses.add(source.harness)
            continue
        records.extend(source_records)
        for record in source_records:
            try:
                live[_search_cache_path(record, resolved)] = _record_fingerprint(record)
            except HistoryError:
                continue

    removed: list[Path] = []
    compiled_root = cache_root / "compiled"
    if compiled_root.exists():
        ensure_private_cache_path(cache_root, compiled_root)
        for harness_dir in sorted(compiled_root.iterdir()):
            if not harness_dir.is_dir() or harness_dir.is_symlink():
                if harness_dir.is_symlink():
                    raise HistoryError(f"Refusing symlinked VCC cache directory: {harness_dir}")
                continue
            ensure_private_cache_path(cache_root, harness_dir)
            if harness_dir.name in unavailable_harnesses:
                continue
            for digest_dir in sorted(harness_dir.iterdir()):
                if not digest_dir.is_dir() or digest_dir.is_symlink():
                    if digest_dir.is_symlink():
                        raise HistoryError(
                            f"Refusing symlinked VCC cache directory: {digest_dir}"
                        )
                    continue
                expected = live.get(digest_dir)
                actual = _read_fingerprint(digest_dir / _FINGERPRINT_FILE)
                expired = _cache_entry_expired(digest_dir, max_age_days)
                if expected is not None and actual == expected and not expired:
                    continue
                ensure_private_cache_path(cache_root, digest_dir)
                with exclusive_cache_lock(digest_dir / _LOCK_FILE) as acquired:
                    if not acquired:
                        continue
                    shutil.rmtree(digest_dir)
                removed.append(digest_dir)
            try:
                harness_dir.rmdir()
            except OSError:
                pass
        try:
            compiled_root.rmdir()
        except OSError:
            pass

    if "t3code" not in unavailable_harnesses:
        removed.extend(_prune_t3_export_cache(records, resolved, max_age_days))
    return removed


def _prune_t3_export_cache(
    records: list[SessionRecord],
    config: HistoryConfig,
    max_age_days: float | None,
) -> list[Path]:
    cache_root = Path(canonical_path(config.cache_dir))
    t3_root = cache_root / "t3code"
    if not t3_root.exists():
        return []
    ensure_private_cache_path(cache_root, t3_root)

    live: dict[Path, str] = {}
    for record in records:
        if record.get("harness") != "t3code":
            continue
        parsed = t3code.parse_ref(record["path"])
        if parsed is None:
            continue
        db_path, thread_id = parsed
        try:
            live[t3code._export_path(db_path, thread_id, cache_root)] = t3code.export_fingerprint(
                db_path
            )
        except HistoryError:
            continue

    removed: list[Path] = []
    for db_dir in sorted(t3_root.iterdir()):
        if db_dir.is_symlink():
            raise HistoryError(f"Refusing symlinked VCC cache directory: {db_dir}")
        if not db_dir.is_dir():
            continue
        ensure_private_cache_path(cache_root, db_dir)
        for out_path in sorted(db_dir.glob("*.jsonl")):
            fingerprint_path = t3code._export_fingerprint_path(out_path)
            expected = live.get(out_path)
            actual = _read_fingerprint(fingerprint_path)
            expired = _path_expired(fingerprint_path, max_age_days)
            if expected is not None and actual == expected and not expired:
                continue
            lock_path = t3code._export_lock_path(out_path)
            with exclusive_cache_lock(lock_path) as acquired:
                if not acquired:
                    continue
                out_path.unlink(missing_ok=True)
                fingerprint_path.unlink(missing_ok=True)
            lock_path.unlink(missing_ok=True)
            removed.append(out_path)
        try:
            db_dir.rmdir()
        except OSError:
            pass
    try:
        t3_root.rmdir()
    except OSError:
        pass
    return removed


def clear_cache(config: HistoryConfig | None = None) -> list[Path]:
    resolved = config or HistoryConfig.from_env()
    cache_root = Path(canonical_path(resolved.cache_dir))
    if not cache_root.exists():
        return []
    ensure_private_cache_path(cache_root, cache_root)
    removed: list[Path] = []
    for name in ("compiled", "t3code"):
        tree = cache_root / name
        if not tree.exists() and not tree.is_symlink():
            continue
        ensure_private_cache_path(cache_root, tree)
        shutil.rmtree(tree)
        removed.append(tree)
    return removed


def _read_fingerprint(path: Path) -> str | None:
    if path.is_symlink():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _cache_entry_expired(path: Path, max_age_days: float | None) -> bool:
    fingerprint_path = path / _FINGERPRINT_FILE
    return _path_expired(
        fingerprint_path if fingerprint_path.exists() else path,
        max_age_days,
    )


def _path_expired(path: Path, max_age_days: float | None) -> bool:
    if max_age_days is None:
        return False
    try:
        modified = path.stat().st_mtime
    except OSError:
        return True
    return time.time() - modified > max_age_days * 86400


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
