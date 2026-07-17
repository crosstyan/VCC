from __future__ import annotations

import json
from typing import Mapping, Sequence

from ..events import (
    Attachment,
    Block,
    Conversation,
    Message,
    Segment,
    SystemEvent,
    Text,
    Thinking,
    ToolCall,
    ToolResult,
)

_META_EVENT_FIELDS = {
    **{
        kind: (
            "model",
            "role",
            "thinkingLevel",
            "configured",
            "serviceTier",
            "mode",
            "data",
        )
        for kind in (
            "model_change",
            "thinking_level_change",
            "service_tier_change",
            "mode_change",
        )
    },
    **{
        kind: (
            "targetId",
            "label",
            "customType",
            "data",
            "injectedRules",
            "selectedToolNames",
        )
        for kind in ("label", "custom", "ttsr_injection", "mcp_tool_selection")
    },
}


def _record_segments(
    records: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, tuple[Mapping[str, object], ...]], ...]:
    segments: list[tuple[str, tuple[Mapping[str, object], ...]]] = []
    current: list[Mapping[str, object]] = []
    reason = "original"

    for record in records:
        if record.get("type") == "compaction":
            if current:
                segments.append((reason, tuple(current)))
            current = [record]
            reason = "compaction"
            continue
        current.append(record)

    if current:
        segments.append((reason, tuple(current)))
    return tuple(segments)


def split_record_segments(
    records: Sequence[Mapping[str, object]],
) -> tuple[tuple[Mapping[str, object], ...], ...]:
    return tuple(segment for _, segment in _record_segments(records))


def _timestamp(record: Mapping[str, object], message: object = None) -> str | None:
    value = record.get("timestamp")
    if isinstance(value, str):
        return value
    if isinstance(message, Mapping):
        value = message.get("timestamp")
        if isinstance(value, str):
            return value
    return None


def _attachment(
    kind: str, item: Mapping[str, object], note: str | None = None
) -> Attachment:
    source = item.get("source")
    if not isinstance(source, Mapping):
        source = item

    media_type = source.get("media_type") or source.get("mediaType")
    if not isinstance(media_type, str):
        media_type = None

    path: str | None = None
    source_type = source.get("type")
    data = source.get("data")
    if source_type == "base64" and isinstance(data, str):
        resolved_media_type = media_type or (
            "image/png" if kind == "image" else "application/octet-stream"
        )
        path = f"data:{resolved_media_type};base64,{data}"
    else:
        for key in ("path", "url", "file_id", "fileId"):
            value = source.get(key)
            if isinstance(value, str):
                path = value
                break

    return Attachment(kind=kind, media_type=media_type, path=path, note=note)


def _content_blocks(content: object) -> tuple[Block, ...]:
    if isinstance(content, str):
        return (Text(content),) if content else ()
    if not isinstance(content, list):
        return ()

    blocks: list[Block] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                blocks.append(Text(text))
        elif item_type == "image":
            blocks.append(_attachment("image", item))
        elif item_type in {"document", "file"}:
            blocks.append(_attachment("document", item))
        elif isinstance(item.get("text"), str):
            text = item.get("text")
            if text:
                blocks.append(Text(text))
        elif isinstance(item_type, str) and item_type:
            blocks.append(Attachment(item_type, None, None, None))
    return tuple(blocks)


def _tool_result_blocks(
    message: Mapping[str, object], call_names: Mapping[str, str]
) -> tuple[Block, ...]:
    call_id = message.get("toolCallId")
    if not isinstance(call_id, str):
        call_id = ""
    name = message.get("toolName")
    if not isinstance(name, str):
        name = call_names.get(call_id, "unknown")

    content = message.get("content")
    text_parts = []
    attachments: list[Block] = []
    if isinstance(content, str):
        if content:
            text_parts.append(content)
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, Mapping):
                continue
            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
            elif item_type == "image":
                attachments.append(_attachment("image", item, note=call_id))
            elif item_type in {"document", "file"}:
                attachments.append(_attachment("document", item, note=call_id))
            elif isinstance(item.get("text"), str):
                text = item.get("text")
                if text:
                    text_parts.append(text)
            elif isinstance(item_type, str) and item_type:
                attachments.append(Attachment(item_type, None, None, call_id))

    return (
        ToolResult(
            call_id=call_id,
            name=name,
            text="\n\n".join(text_parts),
            is_error=bool(message.get("isError", False)),
        ),
        *attachments,
    )


def _assistant_blocks(content: object) -> tuple[Block, ...]:
    if isinstance(content, str):
        return (Text(content),) if content else ()
    if not isinstance(content, list):
        return ()

    blocks: list[Block] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                blocks.append(Text(text))
        elif item_type == "thinking":
            thinking = item.get("thinking")
            if isinstance(thinking, str) and thinking:
                blocks.append(Thinking(thinking, redacted=False))
        elif item_type == "redactedThinking":
            blocks.append(Thinking("", redacted=True))
        elif item_type == "toolCall":
            call_id = item.get("id")
            name = item.get("name")
            arguments = item.get("arguments")
            raw_text = None
            if not isinstance(call_id, str):
                call_id = ""
            if not isinstance(name, str):
                name = "unknown"
            if isinstance(arguments, Mapping):
                tool_input: Mapping[str, object] = arguments
            else:
                tool_input = {}
                if arguments not in (None, ""):
                    raw_text = (
                        arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments, ensure_ascii=False)
                    )
            blocks.append(ToolCall(call_id, name, tool_input, raw_text))
        elif item_type == "image":
            blocks.append(_attachment("image", item))
        elif item_type in {"document", "file"}:
            blocks.append(_attachment("document", item))
        elif isinstance(item_type, str) and item_type:
            blocks.append(Attachment(item_type, None, None, None))
    return tuple(blocks)


def _call_names(records: Sequence[Mapping[str, object]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for record in records:
        if record.get("type") != "message":
            continue
        message = record.get("message")
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, Mapping) or item.get("type") != "toolCall":
                continue
            call_id = item.get("id")
            name = item.get("name")
            if isinstance(call_id, str) and isinstance(name, str):
                names[call_id] = name
    return names


def _selected(record: Mapping[str, object], fields: Sequence[str]) -> dict[str, object]:
    return {field: record[field] for field in fields if field in record}


def _details_payload(record: Mapping[str, object]) -> dict[str, object]:
    details = record.get("details")
    return {"details": details} if isinstance(details, Mapping) and details else {}


def _event_payload(
    event_data: dict[str, object],
    segment_index: int,
    event_index: int,
    payload: Mapping[str, object],
) -> None:
    if payload:
        event_data[f"{segment_index}:{event_index}"] = dict(payload)


def _parse_segment(
    records: Sequence[Mapping[str, object]],
    reason: str,
    segment_index: int,
    event_data: dict[str, object],
) -> Segment:
    events = []
    call_names = _call_names(records)

    def append(event: Message | SystemEvent, payload: Mapping[str, object] | None = None) -> None:
        event_index = len(events)
        events.append(event)
        if payload:
            _event_payload(event_data, segment_index, event_index, payload)

    for record in records:
        record_type = record.get("type")
        if record_type == "session":
            continue

        if record_type == "compaction":
            summary = record.get("summary") or record.get("shortSummary") or record.get("content")
            text = summary if isinstance(summary, str) else ""
            append(
                SystemEvent("compaction", text),
                _selected(record, ("shortSummary", "content")),
            )
            continue

        if record_type == "branch_summary":
            summary = record.get("summary")
            append(
                SystemEvent("branch", summary if isinstance(summary, str) else ""),
                _selected(record, ("fromId",)),
            )
            continue

        if record_type == "custom_message":
            custom_type = record.get("customType")
            if not isinstance(custom_type, str):
                custom_type = "custom_message"
            blocks = _content_blocks(record.get("content"))
            payload = _details_payload(record)
            if blocks or payload:
                append(
                    Message(f"custom:{custom_type}", blocks, _timestamp(record)),
                    payload,
                )
            continue

        if record_type == "session_init":
            task = record.get("task")
            append(
                SystemEvent("session_init", task if isinstance(task, str) else ""),
                _selected(record, ("systemPrompt", "task", "tools")),
            )
            continue

        if record_type in _META_EVENT_FIELDS:
            payload = _selected(record, _META_EVENT_FIELDS[record_type])
            primary = next((value for value in payload.values() if isinstance(value, str)), "")
            append(SystemEvent(str(record_type), primary), payload)
            continue

        if record_type != "message":
            continue

        message = record.get("message")
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        timestamp = _timestamp(record, message)

        if role in {"user", "developer"}:
            blocks = _content_blocks(message.get("content"))
            if blocks:
                append(Message(str(role), blocks, timestamp))
            continue

        if role == "assistant":
            blocks = _assistant_blocks(message.get("content"))
            if blocks:
                append(Message("assistant", blocks, timestamp))
            continue

        if role == "toolResult":
            blocks = _tool_result_blocks(message, call_names)
            payload = _details_payload(message)
            has_content = any(
                isinstance(block, Attachment)
                or (isinstance(block, ToolResult) and bool(block.text))
                for block in blocks
            )
            if has_content or payload:
                append(Message("tool", blocks, timestamp), payload)
            continue

        if role in {"custom", "hookMessage"}:
            custom_type = message.get("customType")
            if not isinstance(custom_type, str):
                custom_type = str(role)
            blocks = _content_blocks(message.get("content"))
            payload = _details_payload(message)
            append(Message(f"custom:{custom_type}", blocks, timestamp), payload)
            continue

        if role == "branchSummary":
            summary = message.get("summary")
            append(
                SystemEvent("branch", summary if isinstance(summary, str) else ""),
                _selected(message, ("fromId",)),
            )
            continue

        if role == "compactionSummary":
            summary = message.get("summary")
            append(
                SystemEvent("compaction", summary if isinstance(summary, str) else "")
            )
            continue

        if role == "fileMention":
            append(
                SystemEvent("file_mention", ""),
                _selected(message, ("files",)),
            )
            continue

        if role == "bashExecution":
            append(
                SystemEvent("bash_execution", ""),
                _selected(message, ("command", "exitCode", "cancelled", "output")),
            )
            continue

        if role == "pythonExecution":
            append(
                SystemEvent("python_execution", ""),
                _selected(message, ("code", "exitCode", "cancelled", "output")),
            )
            continue

        if isinstance(role, str):
            blocks = _content_blocks(message.get("content"))
            if blocks:
                append(Message(f"custom:{role}", blocks, timestamp))

    return Segment(reason=reason, events=tuple(events))


def parse_events(records: Sequence[Mapping[str, object]]) -> Conversation:
    record_segments = _record_segments(records)
    event_data: dict[str, object] = {}
    segments = tuple(
        _parse_segment(records, reason, segment_index, event_data)
        for segment_index, (reason, records) in enumerate(record_segments)
    )
    session = next(
        (dict(record) for record in records if record.get("type") == "session"),
        {},
    )
    return Conversation(
        harness="omp",
        segments=segments,
        metadata={
            "session": session,
            "event_data": event_data,
            "_extract_attachments": True,
        },
    )
