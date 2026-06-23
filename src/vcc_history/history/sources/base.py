from __future__ import annotations

import os
from typing import Protocol

from ..config import HistoryConfig
from ..models import Harness, SessionRecord


class HistorySource(Protocol):
    harness: Harness

    def collect(self, config: HistoryConfig) -> list[SessionRecord]:
        ...

    def matches_cwd(self, record: SessionRecord, cwd: str, config: HistoryConfig) -> bool:
        ...


class ExactCwdMixin:
    def matches_cwd(self, record: SessionRecord, cwd: str, config: HistoryConfig) -> bool:
        target = os.path.abspath(cwd)
        record_cwd = record.get("cwd")
        return bool(record_cwd and os.path.abspath(record_cwd) == target)
