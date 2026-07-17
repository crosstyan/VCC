from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class Block:
    pass


@dataclass(frozen=True)
class Text(Block):
    text: str


@dataclass(frozen=True)
class Thinking(Block):
    text: str
    redacted: bool


@dataclass(frozen=True)
class ToolCall(Block):
    call_id: str
    name: str
    input: Mapping[str, object]
    raw_text: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _freeze(self.input))


@dataclass(frozen=True)
class ToolResult(Block):
    call_id: str
    name: str
    text: str
    is_error: bool


@dataclass(frozen=True)
class Attachment(Block):
    kind: str
    media_type: str | None
    path: str | None
    note: str | None


@dataclass(frozen=True)
class Event:
    pass


@dataclass(frozen=True)
class Message(Event):
    role: str
    blocks: tuple[Block, ...]
    timestamp: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", tuple(self.blocks))


@dataclass(frozen=True)
class SystemEvent(Event):
    kind: str
    text: str


@dataclass(frozen=True)
class Segment:
    reason: str
    events: tuple[Event, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))


@dataclass(frozen=True)
class Conversation:
    harness: str
    segments: tuple[Segment, ...]
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))
        object.__setattr__(self, "metadata", _freeze(self.metadata))
