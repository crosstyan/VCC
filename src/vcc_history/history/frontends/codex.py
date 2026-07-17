from __future__ import annotations

import json
from typing import Mapping, Sequence

from ..events import Attachment, Block, Conversation, Message, Segment, Text, ToolCall, ToolResult
from ._common import structured_text as _structured_text

_TOOL_CALL_TYPES = {
    "function_call",
    "custom_tool_call",
    "web_search_call",
    "image_generation_call",
    "local_shell_call",
    "tool_search_call",
}
_TOOL_OUTPUT_TYPES = {
    "function_call_output",
    "custom_tool_call_output",
    "tool_search_output",
}
_IMAGE_TYPES = {"input_image", "output_image", "image"}
_DOCUMENT_TYPES = {"input_file", "output_file", "file", "document"}


def _record_segments(
    records: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, tuple[Mapping[str, object], ...]], ...]:
    segments: list[tuple[str, tuple[Mapping[str, object], ...]]] = []
    current: list[Mapping[str, object]] = []
    reason = "original"

    for record in records:
        record_type = record.get("type")
        if record_type == "compacted":
            if current:
                segments.append((reason, tuple(current)))
            current = []
            reason = "compaction"
            continue
        if record_type == "event_msg":
            payload = record.get("payload")
            if isinstance(payload, Mapping) and payload.get("type") == "context_compacted":
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


def _maybe_json_string(value: object) -> object | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except (TypeError, ValueError):
        return None


def _attachment(kind: str, item: Mapping[str, object]) -> Attachment:
    source = item.get("source")
    if not isinstance(source, Mapping):
        source = item

    media_type = source.get("media_type") or source.get("mime_type")
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
        for key in ("path", "url", "image_url", "file_id"):
            value = source.get(key)
            if isinstance(value, str):
                path = value
                break
            if isinstance(value, Mapping):
                nested_url = value.get("url")
                if isinstance(nested_url, str):
                    path = nested_url
                    break

    return Attachment(kind=kind, media_type=media_type, path=path, note=None)


def _message_blocks(content: object) -> tuple[Block, ...]:
    if isinstance(content, str):
        return (Text(content),) if content else ()
    if not isinstance(content, list):
        return ()

    blocks: list[Block] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type in {"input_text", "output_text", "text"}:
            text = item.get("text")
            if isinstance(text, str) and text:
                blocks.append(Text(text))
            continue
        if item_type == "refusal":
            text = item.get("refusal")
            if isinstance(text, str) and text:
                blocks.append(Text(text))
            continue
        if item_type in _IMAGE_TYPES:
            blocks.append(_attachment("image", item))
            continue
        if item_type in _DOCUMENT_TYPES:
            blocks.append(_attachment("document", item))
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            blocks.append(Text(text))
        else:
            blocks.append(Text(f"[{item_type or 'content'}]"))
    return tuple(blocks)


def _message_role(value: object) -> str:
    if isinstance(value, str):
        if value in {"user", "assistant", "system", "tool", "developer"}:
            return value
        return f"custom:{value}"
    return "custom:unknown"


def _tool_call_name(payload: Mapping[str, object]) -> str:
    payload_type = payload.get("type")
    if payload_type == "function_call":
        name = payload.get("name")
        return name if isinstance(name, str) else "unknown"
    if payload_type == "custom_tool_call":
        for key in ("name", "call_name", "raw_name"):
            name = payload.get(key)
            if isinstance(name, str) and name:
                return name
        return "custom_tool"
    names = {
        "web_search_call": "web_search",
        "image_generation_call": "image_generation",
        "local_shell_call": "local_shell",
        "tool_search_call": "tool_search",
    }
    if payload_type in names:
        return names[payload_type]
    name = payload.get("name")
    if isinstance(name, str):
        return name
    return str(payload_type or "unknown")


def _tool_call_id(payload: Mapping[str, object]) -> str:
    for key in ("call_id", "id", "tool_call_id"):
        call_id = payload.get(key)
        if isinstance(call_id, str) and call_id:
            return call_id
    return ""


def _raw_tool_input(value: object) -> tuple[Mapping[str, object], str | None]:
    if isinstance(value, Mapping):
        return value, None
    parsed = _maybe_json_string(value)
    if isinstance(parsed, Mapping):
        return parsed, None
    if isinstance(value, str) and value:
        return {}, value
    if value not in (None, ""):
        return {}, json.dumps(value, ensure_ascii=False)
    return {}, None


def _tool_input(payload: Mapping[str, object]) -> tuple[Mapping[str, object], str | None]:
    payload_type = payload.get("type")
    if payload_type == "function_call":
        return _raw_tool_input(payload.get("arguments"))
    if payload_type == "custom_tool_call":
        return _raw_tool_input(payload.get("input"))
    if payload_type == "web_search_call":
        query = payload.get("query")
        return ({"query": query}, None) if query else ({}, None)
    if payload_type == "image_generation_call":
        prompt = payload.get("prompt") or payload.get("revised_prompt")
        return ({"prompt": prompt}, None) if prompt else ({}, None)
    if payload_type == "local_shell_call":
        command = payload.get("command")
        return ({"command": command}, None) if command else ({}, None)
    if payload_type == "tool_search_call":
        query = payload.get("query")
        return ({"query": query}, None) if query else ({}, None)
    return {}, None


def _call_names(records: Sequence[Mapping[str, object]]) -> dict[str, str]:
    names = {}
    for record in records:
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload")
        if not isinstance(payload, Mapping) or payload.get("type") not in _TOOL_CALL_TYPES:
            continue
        call_id = _tool_call_id(payload)
        if call_id:
            names[call_id] = _tool_call_name(payload)
    return names


def _parse_segment(
    records: Sequence[Mapping[str, object]], reason: str
) -> Segment:
    call_names = _call_names(records)
    events = []

    for record in records:
        record_type = record.get("type")
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            continue

        if record_type == "response_item":
            payload_type = payload.get("type")
            if payload_type == "message":
                blocks = _message_blocks(payload.get("content"))
                if blocks:
                    events.append(
                        Message(
                            role=_message_role(payload.get("role")),
                            blocks=blocks,
                            timestamp=_timestamp(record),
                        )
                    )
                continue

            if payload_type in _TOOL_CALL_TYPES:
                tool_input, raw_text = _tool_input(payload)
                events.append(
                    Message(
                        role="assistant",
                        blocks=(
                            ToolCall(
                                call_id=_tool_call_id(payload),
                                name=_tool_call_name(payload),
                                input=tool_input,
                                raw_text=raw_text,
                            ),
                        ),
                        timestamp=_timestamp(record),
                    )
                )
                continue

            if payload_type in _TOOL_OUTPUT_TYPES:
                call_id = payload.get("call_id")
                if not isinstance(call_id, str):
                    call_id = ""
                text = _structured_text(payload.get("output"))
                if text:
                    events.append(
                        Message(
                            role="tool",
                            blocks=(
                                ToolResult(
                                    call_id=call_id,
                                    name=call_names.get(call_id, "unknown"),
                                    text=text,
                                    is_error=False,
                                ),
                            ),
                            timestamp=_timestamp(record),
                        )
                    )
                continue

        if record_type == "event_msg" and payload.get("type") == "web_search_end":
            call_id = payload.get("call_id")
            if not isinstance(call_id, str):
                call_id = ""
            text = _structured_text(
                {"query": payload.get("query", ""), "action": payload.get("action")}
            )
            if text:
                events.append(
                    Message(
                        role="tool",
                        blocks=(
                            ToolResult(
                                call_id=call_id,
                                name="web_search",
                                text=text,
                                is_error=False,
                            ),
                        ),
                        timestamp=_timestamp(record),
                    )
                )

    return Segment(reason=reason, events=tuple(events))


def _metadata(records: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    for record in records:
        if record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if isinstance(payload, Mapping):
            return payload
    return {}


def parse_events(records: Sequence[Mapping[str, object]]) -> Conversation:
    record_segments = _record_segments(records)
    segments = tuple(
        _parse_segment(segment, reason=reason)
        for reason, segment in record_segments
    )
    metadata = dict(_metadata(records))
    metadata["_extract_attachments"] = True
    return Conversation(
        harness="codex",
        segments=segments,
        metadata=metadata,
    )
