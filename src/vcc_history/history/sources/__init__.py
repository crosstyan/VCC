from __future__ import annotations

from ..models import Harness, SOURCE_ORDER
from .base import HistorySource
from .claude import ClaudeSource
from .codex import CodexSource
from .omp import OmpSource
from .reasonix import ReasonixSource
from .t3code import T3CodeSource

SOURCES: dict[Harness, HistorySource] = {
    "claude": ClaudeSource(),
    "codex": CodexSource(),
    "omp": OmpSource(),
    "t3code": T3CodeSource(),
    "reasonix": ReasonixSource(),
}


def selected_sources(harness: str) -> list[HistorySource]:
    if harness == "all":
        return [SOURCES[name] for name in SOURCE_ORDER]
    return [SOURCES[harness]]  # type: ignore[index]
