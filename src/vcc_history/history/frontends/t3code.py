from __future__ import annotations

from typing import Mapping, Sequence

from ..events import Conversation, Event, Message, Segment, SystemEvent, Text


def _timestamp(record: Mapping[str, object]) -> str | None:
    value = record.get("timestamp")
    return value if isinstance(value, str) else None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _details(record: Mapping[str, object]) -> Mapping[str, object]:
    value = record.get("details")
    return value if isinstance(value, Mapping) else {}


def parse_events(records: Sequence[Mapping[str, object]]) -> Conversation:
    events: list[Event] = []
    event_data: list[Mapping[str, object]] = []

    for record in records:
        record_type = record.get("type")

        if record_type == "t3_session":
            fields = {
                key: record[key]
                for key in ("id", "timestamp", "cwd", "title", "model")
                if record.get(key) is not None
            }
            if fields:
                events.append(SystemEvent(kind="session_init", text=""))
                event_data.append({"fields": fields})
            continue

        if record_type == "t3_message":
            source_role = record.get("role")
            role = source_role if isinstance(source_role, str) and source_role else "unknown"
            if role not in {"developer", "user", "assistant"}:
                role = f"custom:message:{role}"
            content = _text(record.get("content"))
            if content:
                events.append(Message(role, (Text(content),), _timestamp(record)))
                event_data.append({})
            continue

        if record_type in {"t3_custom_message", "t3_activity"}:
            custom_type = record.get("customType")
            if not isinstance(custom_type, str) or not custom_type:
                custom_type = str(record_type)
            content = _text(record.get("content"))
            details = _details(record)
            if content or details:
                blocks = (Text(content),) if content else ()
                events.append(
                    Message(
                        role=f"custom:{custom_type}",
                        blocks=blocks,
                        timestamp=_timestamp(record),
                    )
                )
                event_data.append({"details": details} if details else {})

    segments = (Segment(reason="original", events=tuple(events)),) if records else ()
    return Conversation(
        harness="t3code",
        segments=segments,
        metadata={"event_data": tuple(event_data)},
    )
