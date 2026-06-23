from __future__ import annotations

import glob
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Literal, TypedDict, cast

import click

Harness = Literal["omp", "codex"]
HarnessFilter = Literal["all", "omp", "codex"]


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


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


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


def collect_sessions(harness: HarnessFilter = "all") -> list[SessionRecord]:
    records: list[SessionRecord] = []
    if harness in {"all", "codex"}:
        pattern = str(_codex_home() / "sessions" / "**" / "*.jsonl")
        for path in glob.glob(pattern, recursive=True):
            record = _codex_record(path)
            if record:
                records.append(record)
    if harness in {"all", "omp"}:
        pattern = str(_omp_agent_dir() / "sessions" / "**" / "*.jsonl")
        for path in glob.glob(pattern, recursive=True):
            record = _omp_record(path)
            if record:
                records.append(record)
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
@click.option("--harness", type=click.Choice(["all", "omp", "codex"]), default="all", show_default=True)
@click.option("--cwd", type=click.Path(path_type=str), help="Filter by exact session cwd.")
@click.option("--latest", is_flag=True, help="Return only the newest matching session.")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def list_cmd(harness: HarnessFilter, cwd: str | None, latest: bool, limit: int, as_json: bool) -> None:
    """List OMP and Codex session files."""
    records = filter_sessions(collect_sessions(harness), cwd, latest, limit)
    _print_records(records, as_json)


@main.command()
@click.argument("pattern")
@click.option("--harness", type=click.Choice(["all", "omp", "codex"]), default="all", show_default=True)
@click.option("--cwd", type=click.Path(path_type=str), help="Filter by exact session cwd.")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--ignore-case", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def search(pattern: str, harness: HarnessFilter, cwd: str | None, limit: int, ignore_case: bool, as_json: bool) -> None:
    """Search recent OMP and Codex sessions."""
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
