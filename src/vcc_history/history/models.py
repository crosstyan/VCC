from __future__ import annotations

from typing import Literal, TypedDict

Harness = str
HarnessFilter = str
SessionKind = Literal["main", "delegated"]


class SessionRecord(TypedDict, total=False):
    harness: Harness
    path: str
    session_id: str | None
    created_at: str | None
    updated_at: str | None
    timestamp: str | None
    kind: SessionKind
    cwd: str | None
    title: str | None
    source: str | None
    provider: str | None
    model: str | None
    alias_keys: list[str]


class SearchHit(TypedDict):
    harness: Harness
    session_path: str
    compiled_path: str


class SearchMatch(SearchHit):
    block_type: str
    ref: str
    lines: list[str]
