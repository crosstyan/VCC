from __future__ import annotations

from typing import Literal, TypedDict

Harness = Literal["claude", "omp", "codex", "t3code", "reasonix"]
HarnessFilter = Literal["all", "claude", "omp", "codex", "t3code", "reasonix"]

HARNESS_CHOICES: tuple[str, ...] = ("all", "claude", "omp", "codex", "t3code", "reasonix")
SOURCE_ORDER: tuple[Harness, ...] = ("claude", "codex", "omp", "t3code", "reasonix")


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
