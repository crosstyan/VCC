#!/usr/bin/env python3
"""List Codex rollout files from ~/.codex/sessions."""

import argparse
import glob
import json
import os
import sys


def _codex_home():
    return os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))


def _read_session_meta(path):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") != "session_meta":
                    return None
                payload = obj.get("payload", {})
                return {
                    "path": os.path.abspath(path),
                    "thread_id": payload.get("id"),
                    "timestamp": payload.get("timestamp") or obj.get("timestamp"),
                    "cwd": payload.get("cwd"),
                    "source": payload.get("source"),
                }
    except (OSError, ValueError, TypeError):
        return None
    return None


def _collect_rollouts():
    pattern = os.path.join(_codex_home(), "sessions", "**", "*.jsonl")
    records = []
    for path in glob.glob(pattern, recursive=True):
        meta = _read_session_meta(path)
        if meta:
            records.append(meta)
    records.sort(key=lambda rec: (rec.get("timestamp") or "", rec["path"]))
    return records


def main():
    parser = argparse.ArgumentParser(description="Find Codex rollout files")
    parser.add_argument("--cwd", help="Filter by exact session cwd")
    parser.add_argument("--latest", action="store_true", help="Return only the newest match")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of matches")
    parser.add_argument(
        "--format",
        choices=("plain", "json"),
        default="plain",
        help="Output format",
    )
    args = parser.parse_args()

    matches = _collect_rollouts()
    if args.cwd:
        target = os.path.abspath(args.cwd)
        matches = [rec for rec in matches if rec.get("cwd") == target]

    if args.latest:
        matches = matches[-1:] if matches else []
    elif args.limit > 0:
        matches = matches[-args.limit:]

    if args.format == "json":
        json.dump(matches, sys.stdout, ensure_ascii=False, indent=2)
        if matches:
            sys.stdout.write("\n")
        return

    for rec in matches:
        print(rec["path"])


if __name__ == "__main__":
    main()
