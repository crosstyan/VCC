from __future__ import annotations

import io
import json
import re
import sys

import click

from .history.errors import HistoryError
from .history.models import HARNESS_CHOICES, HarnessFilter, SearchHit, SessionRecord
from .history.refs import read_ref
from .history.service import collect_sessions, compile_session, filter_sessions, search_sessions


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Cross-harness VCC history CLI."""
    _ensure_stdout_utf8()


@main.command("list")
@click.option("--harness", type=click.Choice(HARNESS_CHOICES), default="all", show_default=True)
@click.option("--cwd", type=click.Path(path_type=str), help="Filter by exact session cwd.")
@click.option("--latest", is_flag=True, help="Return only the newest matching session.")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option(
    "--include-subagents",
    is_flag=True,
    help="Include nested Claude Code subagent transcripts.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def list_cmd(
    harness: HarnessFilter,
    cwd: str | None,
    latest: bool,
    limit: int,
    include_subagents: bool,
    as_json: bool,
) -> None:
    """List Claude Code, Codex, OMP, T3 Code, and Reasonix sessions."""
    try:
        records = filter_sessions(
            collect_sessions(harness, include_subagents=include_subagents), cwd, latest, limit
        )
    except HistoryError as error:
        raise click.ClickException(str(error)) from error
    _print_records(records, as_json)


@main.command()
@click.argument("pattern")
@click.option("--harness", type=click.Choice(HARNESS_CHOICES), default="all", show_default=True)
@click.option("--cwd", type=click.Path(path_type=str), help="Filter by exact session cwd.")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--ignore-case", is_flag=True)
@click.option(
    "--include-subagents",
    is_flag=True,
    help="Include nested Claude Code subagent transcripts.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def search(
    pattern: str,
    harness: HarnessFilter,
    cwd: str | None,
    limit: int,
    ignore_case: bool,
    include_subagents: bool,
    as_json: bool,
) -> None:
    """Search recent Claude Code, Codex, OMP, T3 Code, and Reasonix sessions."""
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as error:
        raise click.BadParameter(f"invalid regex: {error}", param_hint="pattern") from error
    try:
        records = filter_sessions(
            collect_sessions(harness, include_subagents=include_subagents), cwd, False, limit
        )
        hits = search_sessions(records, compiled)
    except HistoryError as error:
        raise click.ClickException(str(error)) from error
    _print_hits(hits, as_json)


@main.command("compile")
@click.argument("inputs", nargs=-1, required=True, type=click.Path(path_type=str))
@click.option("-o", "--output-dir", type=click.Path(path_type=str))
def compile_cmd(inputs: tuple[str, ...], output_dir: str | None) -> None:
    """Compile one or more session files."""
    try:
        for input_path in inputs:
            compile_session(input_path, output_dir=output_dir)
    except HistoryError as error:
        raise click.ClickException(str(error)) from error


@main.command("read")
@click.argument("ref")
def read_cmd(ref: str) -> None:
    """Read a compiled transcript ref such as path.txt:10-30."""
    sys.stdout.write(read_ref(ref))


def _print_records(records: list[SessionRecord], as_json: bool) -> None:
    if as_json:
        json.dump(records, sys.stdout, ensure_ascii=False, indent=2)
        if records:
            sys.stdout.write("\n")
        return
    for rec in records:
        cwd = f" cwd={rec['cwd']}" if rec.get("cwd") else ""
        title = f" title={rec['title']}" if rec.get("title") else ""
        source = (
            " (subagent)"
            if rec["harness"] == "claude" and rec.get("source") == "subagent"
            else ""
        )
        print(f"{rec['harness']}{source}\t{rec.get('timestamp') or '-'}\t{rec['path']}{cwd}{title}")


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


def _ensure_stdout_utf8() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


if __name__ == "__main__":
    main()
