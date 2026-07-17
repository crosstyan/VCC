from __future__ import annotations

import re
from pathlib import Path


def read_ref(ref: str) -> str:
    path_text, ranges = split_ref(ref)
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


def split_ref(ref: str) -> tuple[str, list[tuple[int, int]] | None]:
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
