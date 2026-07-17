from __future__ import annotations

import json
from typing import Iterable, Mapping, Sequence

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

_DISCARD_TYPES = {"queue-operation", "file-history-snapshot", "last-prompt", "progress"}
_DISCARD_SYSTEM_SUBTYPES = {
    "stop_hook_summary",
    "api_error",
    "bridge_status",
    "informational",
    "local_command",
}


def _discard(record: Mapping[str, object]) -> bool:
    record_type = record.get("type")
    return record_type in _DISCARD_TYPES or (
        record_type == "system" and record.get("subtype") in _DISCARD_SYSTEM_SUBTYPES
    )


def _merge_chunks(records: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    merged: list[Mapping[str, object]] = []
    active_message_id: object = None
    active_index: int | None = None

    for record in records:
        if record.get("type") != "assistant":
            merged.append(record)
            if not _discard(record):
                active_message_id = None
                active_index = None
            continue

        message = record.get("message")
        if not isinstance(message, Mapping):
            merged.append(record)
            active_message_id = None
            active_index = None
            continue

        message_id = message.get("id")
        if message_id and message_id == active_message_id and active_index is not None:
            active_record = merged[active_index]
            active_message = active_record.get("message")
            if not isinstance(active_message, dict):
                continue
            active_content = active_message.get("content")
            chunk_content = message.get("content")
            if isinstance(active_content, list) and isinstance(chunk_content, list):
                active_content.extend(chunk_content)
            if message.get("stop_reason"):
                active_message["stop_reason"] = message["stop_reason"]
            continue

        copied_record = dict(record)
        copied_message = dict(message)
        content = copied_message.get("content")
        if isinstance(content, list):
            copied_message["content"] = list(content)
        copied_record["message"] = copied_message
        merged.append(copied_record)
        if message_id:
            active_message_id = message_id
            active_index = len(merged) - 1
        else:
            active_message_id = None
            active_index = None

    return merged


def _record_segments(
    records: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, tuple[Mapping[str, object], ...]], ...]:
    merged = _merge_chunks(records)
    segments: list[tuple[str, tuple[Mapping[str, object], ...]]] = []
    current: list[Mapping[str, object]] = []
    reason = "original"

    for record in merged:
        if _discard(record):
            continue
        if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
            if current:
                segments.append((reason, tuple(current)))
            current = []
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


def _timestamp(record: Mapping[str, object]) -> str | None:
    value = record.get("timestamp")
    return value if isinstance(value, str) else None


def _attachment(kind: str, source: object, note: str | None = None) -> Attachment:
    if not isinstance(source, Mapping):
        return Attachment(kind=kind, media_type=None, path=None, note=note)

    media_type = source.get("media_type")
    if not isinstance(media_type, str):
        media_type = None

    path: str | None = None
    if source.get("type") == "base64":
        data = source.get("data")
        if isinstance(data, str):
            resolved_media_type = media_type or (
                "image/png" if kind == "image" else "application/octet-stream"
            )
            path = f"data:{resolved_media_type};base64,{data}"
    else:
        for key in ("path", "url", "file_id"):
            value = source.get(key)
            if isinstance(value, str):
                path = value
                break

    return Attachment(kind=kind, media_type=media_type, path=path, note=note)


def _tool_result_blocks(
    block: Mapping[str, object], call_names: Mapping[str, str]
) -> list[Block]:
    call_id = block.get("tool_use_id")
    if not isinstance(call_id, str):
        call_id = ""
    name = call_names.get(call_id, "unknown")
    is_error = bool(block.get("is_error", False))
    content = block.get("content", "")

    if isinstance(content, str):
        return [ToolResult(call_id=call_id, name=name, text=content, is_error=is_error)]
    if not isinstance(content, list):
        return [ToolResult(call_id=call_id, name=name, text="", is_error=is_error)]

    text_parts = []
    attachments: list[Block] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        elif item_type == "image":
            attachments.append(_attachment("image", item.get("source"), note=call_id))
        elif item_type == "document":
            attachments.append(
                _attachment("document", item.get("source"), note=call_id)
            )
    return [
        ToolResult(
            call_id=call_id,
            name=name,
            text="\n\n".join(text_parts),
            is_error=is_error,
        ),
        *attachments,
    ]


def _content_blocks(
    content: object, call_names: Mapping[str, str], include_tool_results: bool = False
) -> tuple[Block, ...]:
    if isinstance(content, str):
        return (Text(content),) if content else ()
    if not isinstance(content, list):
        return ()

    blocks: list[Block] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        block_type = block.get("type")
        if block_type == "thinking":
            text = block.get("thinking")
            if isinstance(text, str) and text:
                blocks.append(Thinking(text=text, redacted=False))
        elif block_type == "redacted_thinking":
            blocks.append(Thinking(text="", redacted=True))
        elif block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                blocks.append(Text(text))
        elif block_type == "tool_use":
            call_id = block.get("id")
            name = block.get("name")
            tool_input = block.get("input")
            raw_text = None
            if not isinstance(call_id, str):
                call_id = ""
            if not isinstance(name, str):
                name = "unknown"
            if isinstance(tool_input, Mapping):
                parsed_input: Mapping[str, object] = tool_input
            else:
                parsed_input = {}
                if tool_input not in (None, ""):
                    raw_text = json.dumps(tool_input, ensure_ascii=False)
            blocks.append(
                ToolCall(
                    call_id=call_id,
                    name=name,
                    input=parsed_input,
                    raw_text=raw_text,
                )
            )
        elif block_type == "tool_result" and include_tool_results:
            blocks.extend(_tool_result_blocks(block, call_names))
        elif block_type == "image":
            blocks.append(_attachment("image", block.get("source")))
        elif block_type == "document":
            blocks.append(_attachment("document", block.get("source")))
    return tuple(blocks)


def _call_names(
    segments: Sequence[Sequence[Mapping[str, object]]],
) -> dict[str, str]:
    names: dict[str, str] = {}
    for segment in segments:
        for record in segment:
            if record.get("type") != "assistant":
                continue
            message = record.get("message")
            if not isinstance(message, Mapping):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, Mapping) or block.get("type") != "tool_use":
                    continue
                call_id = block.get("id")
                name = block.get("name")
                if isinstance(call_id, str) and isinstance(name, str):
                    names[call_id] = name
    return names


def _parse_segment(
    records: Sequence[Mapping[str, object]],
    reason: str,
    call_names: Mapping[str, str],
) -> Segment:
    events = []
    for record in records:
        record_type = record.get("type")

        if record_type == "system":
            content = record.get("content")
            if not content:
                message = record.get("message")
                if isinstance(message, Mapping):
                    content = message.get("content")
            blocks = _content_blocks(content, call_names)
            if blocks:
                events.append(Message("system", blocks, _timestamp(record)))
            continue

        if record_type == "user":
            message = record.get("message")
            if not isinstance(message, Mapping):
                continue
            content = message.get("content", "")
            if record.get("isCompactSummary"):
                text = content if isinstance(content, str) else ""
                events.append(SystemEvent(kind="compact_summary", text=text))
                continue
            blocks = _content_blocks(content, call_names, include_tool_results=True)
            if blocks:
                events.append(Message("user", blocks, _timestamp(record)))
            continue

        if record_type == "assistant":
            message = record.get("message")
            if not isinstance(message, Mapping):
                continue
            blocks = _content_blocks(message.get("content", []), call_names)
            if blocks:
                events.append(Message("assistant", blocks, _timestamp(record)))

    return Segment(reason=reason, events=tuple(events))


def parse_events(records: Sequence[Mapping[str, object]]) -> Conversation:
    record_segments = _record_segments(records)
    segments = tuple(
        _parse_segment(
            segment,
            reason=reason,
            call_names=_call_names([segment]),
        )
        for reason, segment in record_segments
    )
    return Conversation(
        harness="claude",
        segments=segments,
        metadata={"_extract_attachments": True},
    )
