from __future__ import annotations

import json
from typing import Mapping, Sequence

from ..events import (
    Attachment,
    Block,
    Conversation,
    Message,
    Segment,
    Text,
    Thinking,
    ToolCall,
    ToolResult,
)
from ._common import structured_text as _structured_text


def _timestamp(record: Mapping[str, object]) -> str | None:
    value = record.get("timestamp")
    return value if isinstance(value, str) else None


def _attachment(kind: str, source: object, note: str | None = None) -> Attachment:
    media_type: str | None = None
    path: str | None = None

    if isinstance(source, str):
        path = source
        if source.startswith("data:") and ";base64," in source:
            media_type = source[5:].split(";base64,", 1)[0] or None
    elif isinstance(source, Mapping):
        nested_source = source.get("source")
        if isinstance(nested_source, (str, Mapping)):
            return _attachment(kind, nested_source, note=note)

        value = source.get("media_type")
        if isinstance(value, str):
            media_type = value

        if source.get("type") == "base64":
            data = source.get("data")
            if isinstance(data, str):
                resolved_media_type = media_type or (
                    "image/png" if kind == "image" else "application/octet-stream"
                )
                path = f"data:{resolved_media_type};base64,{data}"
        else:
            for key in ("path", "url", "file_id", "data"):
                value = source.get(key)
                if isinstance(value, str):
                    path = value
                    break

    return Attachment(kind=kind, media_type=media_type, path=path, note=note)


def _content_blocks(content: object) -> tuple[Block, ...]:
    if isinstance(content, str):
        return (Text(content),) if content else ()
    if isinstance(content, Mapping):
        text = _structured_text(content)
        return (Text(text),) if text else ()
    if not isinstance(content, list):
        return ()

    blocks: list[Block] = []
    for item in content:
        if isinstance(item, str):
            if item:
                blocks.append(Text(item))
            continue
        if not isinstance(item, Mapping):
            continue

        text = item.get("text")
        if isinstance(text, str):
            if text:
                blocks.append(Text(text))
            continue

        item_type = item.get("type")
        if item_type == "image":
            blocks.append(_attachment("image", item))
        elif item_type in {"document", "file"}:
            blocks.append(_attachment("document", item))
        elif isinstance(item_type, str) and item_type:
            blocks.append(Text(f"[{item_type}]"))
    return tuple(blocks)


def _image_blocks(images: object) -> tuple[Attachment, ...]:
    if not isinstance(images, list):
        return ()
    return tuple(_attachment("image", image) for image in images if image)


def _tool_input(arguments: object) -> Mapping[str, object]:
    if isinstance(arguments, Mapping):
        return arguments
    if isinstance(arguments, str):
        stripped = arguments.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError):
            return {"arguments": arguments}
        if isinstance(parsed, Mapping):
            return parsed
        if isinstance(parsed, list):
            return {"arguments": parsed}
        return {"arguments": arguments}
    if isinstance(arguments, list):
        return {"arguments": arguments}
    return {}


def _tool_call(call: Mapping[str, object]) -> ToolCall:
    function = call.get("function")
    function_data = function if isinstance(function, Mapping) else {}

    call_id = call.get("id")
    name = call.get("name") or function_data.get("name")
    arguments = call.get("arguments")
    if arguments is None:
        arguments = function_data.get("arguments")

    return ToolCall(
        call_id=call_id if isinstance(call_id, str) else "",
        name=name if isinstance(name, str) and name else "unknown",
        input=_tool_input(arguments),
        raw_text=None,
    )


def _call_names(records: Sequence[Mapping[str, object]]) -> dict[str, str]:
    names = {}
    for record in records:
        if record.get("role") != "assistant":
            continue
        calls = record.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            parsed = _tool_call(call)
            if parsed.call_id:
                names[parsed.call_id] = parsed.name
    return names


def _is_tool_error(content: object) -> bool:
    text = _structured_text(content)
    lowered = text.strip().lower()
    return (
        lowered.startswith("error:")
        or lowered.startswith("blocked:")
        or "permission denied" in lowered
    )


def _tool_result_blocks(
    record: Mapping[str, object], call_names: Mapping[str, str]
) -> tuple[Block, ...]:
    call_id = record.get("tool_call_id")
    if not isinstance(call_id, str):
        call_id = ""
    name = record.get("name")
    if not isinstance(name, str) or not name:
        name = call_names.get(call_id, "unknown")

    content = record.get("content")
    content_blocks = _content_blocks(content)
    text_parts = [block.text for block in content_blocks if isinstance(block, Text)]
    attachments = [
        Attachment(
            kind=block.kind,
            media_type=block.media_type,
            path=block.path,
            note=call_id,
        )
        for block in content_blocks
        if isinstance(block, Attachment)
    ]
    return (
        ToolResult(
            call_id=call_id,
            name=name,
            text="\n\n".join(text_parts),
            is_error=_is_tool_error(content),
        ),
        *attachments,
    )


def parse_events(records: Sequence[Mapping[str, object]]) -> Conversation:
    call_names = _call_names(records)
    events = []

    for record in records:
        role = record.get("role")
        if role in {"system", "user"}:
            blocks = (*_content_blocks(record.get("content")), *_image_blocks(record.get("images")))
            if blocks:
                events.append(Message(str(role), blocks, _timestamp(record)))
            continue

        if role == "assistant":
            blocks: list[Block] = []
            reasoning = record.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                blocks.append(Thinking(text=reasoning, redacted=False))
            blocks.extend(_content_blocks(record.get("content")))
            blocks.extend(_image_blocks(record.get("images")))

            calls = record.get("tool_calls")
            if isinstance(calls, list):
                blocks.extend(
                    _tool_call(call) for call in calls if isinstance(call, Mapping)
                )
            if blocks:
                events.append(Message("assistant", tuple(blocks), _timestamp(record)))
            continue

        if role == "tool":
            blocks = _tool_result_blocks(record, call_names)
            if any(
                isinstance(block, Attachment)
                or (isinstance(block, ToolResult) and bool(block.text))
                for block in blocks
            ):
                events.append(Message("tool", blocks, _timestamp(record)))

    segments = (Segment(reason="original", events=tuple(events)),) if events else ()
    return Conversation(harness="reasonix", segments=segments, metadata={})
