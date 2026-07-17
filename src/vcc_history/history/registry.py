from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import Harness
from .sources.base import HistorySource
from .sources.claude import ClaudeSource
from .sources.codex import CodexSource
from .sources.omp import OmpSource
from .sources.reasonix import ReasonixSource
from .sources.t3code import T3CodeSource


@dataclass(frozen=True)
class HarnessDescriptor:
    id: Harness
    label: str
    source_factory: Callable[[], HistorySource]


DESCRIPTORS: tuple[HarnessDescriptor, ...] = (
    HarnessDescriptor("claude", "Claude Code", ClaudeSource),
    HarnessDescriptor("codex", "Codex", CodexSource),
    HarnessDescriptor("omp", "OMP", OmpSource),
    HarnessDescriptor("t3code", "T3 Code", T3CodeSource),
    HarnessDescriptor("reasonix", "Reasonix", ReasonixSource),
)

HARNESS_IDS = tuple(descriptor.id for descriptor in DESCRIPTORS)
HARNESS_CHOICES = ("all", *HARNESS_IDS)
SOURCES = {descriptor.id: descriptor.source_factory() for descriptor in DESCRIPTORS}


def selected_sources(harness: Harness) -> list[HistorySource]:
    if harness == "all":
        return [SOURCES[name] for name in HARNESS_IDS]
    return [SOURCES[harness]]
