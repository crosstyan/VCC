from __future__ import annotations

import base64
import os
import re
from typing import Mapping

from .events import (
    Attachment,
    Block,
    Conversation,
    Message,
    SystemEvent,
    Text,
    Thinking,
    ToolCall,
    ToolResult,
)
from .registry import HARNESS_IDS

SEP = "══════════════════════════════"
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_TOOL_SUMMARY_FIELDS = {
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "Agent": "description",
    "Skill": "skill",
    "read": "path",
    "edit": "path",
    "write": "path",
    "find": "pattern",
    "search": "pattern",
    "web_search": "query",
    "task": "description",
    "skill": "skill",
    "exec_command": "cmd",
    "write_stdin": "chars",
    "spawn_agent": "message",
    "send_input": "message",
    "wait_agent": "target",
    "close_agent": "target",
    "view_image": "path",
    "request_user_input": "questions",
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "multi_edit": "path",
    "list_files": "path",
    "grep": "pattern",
}


def _node(node_type: str, content: list[str], **fields: object) -> dict[str, object]:
    node: dict[str, object] = {
        "type": node_type,
        "content": content,
        "searchable": fields.pop("searchable", False),
    }
    node.update(fields)
    return node


def _sanitize(text: str) -> str:
    if not text:
        return text
    if "\r" in text:
        text = text.replace("\r", "")
    if "\x1b" in text:
        text = _ANSI_RE.sub("", text)
    return _CTRL_RE.sub("", text)


def _preprocess_tool_text(text: str, tool_name: str) -> str:
    text = _sanitize(text)
    if tool_name != "Read":
        return text
    lines = []
    for line in text.split("\n"):
        if "→" in line:
            head, tail = line.split("→", 1)
            if head.strip().isdigit():
                line = tail
        lines.append(line)
    return "\n".join(lines)


def _short_tid(tool_id: str) -> str:
    return tool_id[-6:] if len(tool_id) > 6 else tool_id


def _emit_dict(data: Mapping[str, object], indent: int = 0) -> str:
    parts = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, Mapping):
            parts.append(prefix + key + ":")
            parts.append(_emit_dict(value, indent + 2))
        elif isinstance(value, (list, tuple)):
            parts.append(prefix + key + ":")
            for item in value:
                if isinstance(item, Mapping):
                    parts.append(prefix + "  -")
                    parts.append(_emit_dict(item, indent + 4))
                else:
                    parts.append(prefix + "  - " + str(item))
        else:
            text = str(value)
            if "\n" in text:
                parts.append(prefix + key + ": |")
                child_prefix = " " * (indent + 2)
                for line in text.split("\n"):
                    parts.append((child_prefix + line) if line else "")
            else:
                parts.append(prefix + key + ": " + text)
    return "\n".join(parts)


def _tool_summary(name: str, tool_input: Mapping[str, object]) -> str:
    field = _TOOL_SUMMARY_FIELDS.get(name)
    if field and field in tool_input:
        return f'* {name} "{tool_input[field]}"'
    if name in {"Bash", "bash"}:
        value = tool_input.get("description") or tool_input.get("command", "")
        if value:
            text = str(value)
            if not tool_input.get("description") and len(text) > 60:
                text = text[:57] + "..."
            return f'* {name} "{text}"'
    return f"* {name}"


def _media_ext(media_type: str, default_ext: str) -> str:
    if "/" not in media_type:
        return default_ext
    extension = media_type.split("/", 1)[1].split("+", 1)[0]
    if extension == "jpeg":
        return "jpg"
    return extension or default_ext


def _inline_data(attachment: Attachment) -> tuple[str, str] | None:
    path = attachment.path
    if not path or not path.startswith("data:") or ";base64," not in path:
        return None
    descriptor, data = path[5:].split(";base64,", 1)
    media_type = attachment.media_type or descriptor
    return media_type, data


def _extract_attachment(
    attachment: Attachment,
    outdir: str,
    data_prefix: str,
    data_counter: list[int],
) -> str | None:
    inline = _inline_data(attachment)
    if inline is None:
        return None
    media_type, data = inline
    if attachment.kind == "image":
        stem = "img"
        default_media_type = "image/png"
        default_extension = "png"
    else:
        stem = "doc"
        default_media_type = "application/octet-stream"
        default_extension = "bin"
    resolved_media_type = media_type or default_media_type
    filename = (
        f"{data_prefix}_{stem}_{data_counter[0]}."
        f"{_media_ext(resolved_media_type, default_extension)}"
    )
    with open(os.path.join(outdir, filename), "wb") as output:
        output.write(base64.b64decode(data))
    data_counter[0] += 1
    return filename


def _omp_session_init_lines(payload: Mapping[str, object]) -> list[str]:
    lines = []
    system_prompt = payload.get("systemPrompt")
    if isinstance(system_prompt, str) and system_prompt:
        lines.append("system_prompt: |")
        lines.extend(f"  {line}" for line in _sanitize(system_prompt).split("\n"))
    task = payload.get("task")
    if isinstance(task, str) and task:
        lines.append("task: |")
        lines.extend(f"  {line}" for line in _sanitize(task).split("\n"))
    tools = payload.get("tools")
    if isinstance(tools, (list, tuple)) and tools:
        lines.append("tools:")
        lines.extend(f"  - {_sanitize(str(tool))}" for tool in tools)
    return lines


def _omp_branch_lines(
    event: SystemEvent, payload: Mapping[str, object]
) -> list[str]:
    lines = []
    from_id = payload.get("fromId")
    if from_id:
        lines.extend([f"from: {from_id}", ""])
    text = _sanitize(event.text)
    if text:
        lines.extend(text.split("\n"))
    return lines


def _omp_file_mention_lines(payload: Mapping[str, object]) -> list[str]:
    files = payload.get("files")
    if not isinstance(files, (list, tuple)):
        return []
    lines = []
    for item in files:
        if not isinstance(item, Mapping):
            continue
        path = item.get("path") or "(unknown)"
        lines.append(f'<file path="{path}">')
        content = item.get("content")
        if isinstance(content, str) and content:
            lines.extend(_sanitize(content).split("\n"))
        elif item.get("image"):
            lines.append("[image]")
        lines.append("</file>")
    return lines


def _omp_execution_lines(
    payload: Mapping[str, object], source_key: str
) -> list[str]:
    source = payload.get(source_key)
    output = payload.get("output")
    lines = []
    if source:
        lines.append(f"{source_key}: |")
        lines.extend(f"  {line}" for line in _sanitize(str(source)).split("\n"))
    if "exitCode" in payload:
        lines.append(f"exit_code: {payload.get('exitCode')}")
    if payload.get("cancelled"):
        lines.append("cancelled: true")
    if output:
        lines.append("output: |")
        lines.extend(f"  {line}" for line in _sanitize(str(output)).split("\n"))
    return lines


def render(
    conversation: Conversation,
    outdir: str,
    data_prefix: str,
    data_counter: list[int] | None = None,
    *,
    segment_index: int = 0,
) -> list[dict[str, object]]:
    if conversation.harness not in HARNESS_IDS:
        raise ValueError(f"unsupported conversation harness: {conversation.harness}")
    if data_counter is None:
        data_counter = [0]

    segment = conversation.segments[segment_index]
    nodes: list[dict[str, object]] = []
    section = 0
    block_index = 0
    event_data = conversation.metadata.get("event_data", ())
    extract_attachments = conversation.metadata.get("_extract_attachments") is True

    def metadata_for_event(index: int) -> Mapping[str, object]:
        if isinstance(event_data, Mapping):
            value = event_data.get(f"{segment_index}:{index}")
            return value if isinstance(value, Mapping) else {}
        if isinstance(event_data, (list, tuple)) and index < len(event_data):
            value = event_data[index]
            if isinstance(value, Mapping):
                return value
        return {}

    def emit_separator() -> None:
        if section > 0:
            nodes.append(_node("meta", ["", SEP]))

    def emit_header(label: str, role: str, kind: str = "message", **fields: object) -> None:
        nodes.append(
            _node(
                "meta_header",
                [label, ""],
                _sec=section,
                _role=role,
                _kind=kind,
                **fields,
            )
        )

    def emit_attachment(attachment: Attachment, text_type: str) -> bool:
        nonlocal block_index
        if attachment.kind == "image":
            filename = None
            if extract_attachments:
                filename = _extract_attachment(
                    attachment, outdir, data_prefix, data_counter
                )
            label = f"[image: {filename}]" if filename else "[image]"
            suffix = "image"
        elif attachment.kind == "document":
            filename = None
            if extract_attachments:
                filename = _extract_attachment(
                    attachment, outdir, data_prefix, data_counter
                )
            label = f"[document: {filename}]" if filename else "[document]"
            suffix = "document"
        else:
            label = f"[{attachment.kind}]"
            suffix = attachment.kind
        nodes.append(
            _node(
                f"{text_type}_{suffix}",
                [label],
                searchable=True,
                _sec=section,
                _blk=block_index,
            )
        )
        block_index += 1
        return True

    def emit_blocks(blocks: tuple[Block, ...], text_type: str) -> bool:
        nonlocal block_index
        has_any = False
        for block in blocks:
            if isinstance(block, Thinking):
                if block.redacted:
                    nodes.append(
                        _node(
                            "meta",
                            [">>>redacted_thinking"],
                            _sec=section,
                            _blk=block_index,
                        )
                    )
                    nodes.append(
                        _node(
                            "redacted_thinking",
                            ["[content redacted by model provider]"],
                            searchable=True,
                            _sec=section,
                            _blk=block_index,
                        )
                    )
                    nodes.append(
                        _node(
                            "meta",
                            ["<<<redacted_thinking"],
                            _sec=section,
                            _blk=block_index,
                        )
                    )
                else:
                    text = _sanitize(block.text)
                    if not text:
                        continue
                    nodes.append(
                        _node(
                            "meta",
                            [">>>thinking"],
                            _sec=section,
                            _blk=block_index,
                        )
                    )
                    nodes.append(
                        _node(
                            "thinking",
                            text.split("\n"),
                            searchable=True,
                            _sec=section,
                            _blk=block_index,
                        )
                    )
                    nodes.append(
                        _node(
                            "meta",
                            ["<<<thinking"],
                            _sec=section,
                            _blk=block_index,
                        )
                    )
                block_index += 1
                has_any = True
                continue

            if isinstance(block, Text):
                text = _sanitize(block.text)
                if not text:
                    continue
                nodes.append(
                    _node(
                        text_type,
                        text.split("\n"),
                        searchable=True,
                        _sec=section,
                        _blk=block_index,
                    )
                )
                block_index += 1
                has_any = True
                continue

            if isinstance(block, ToolCall):
                header = f">>>tool_call {block.name}:{_short_tid(block.call_id)}"
                summary = _tool_summary(block.name, block.input)
                nodes.append(
                    _node(
                        "meta",
                        [header],
                        _sec=section,
                        _blk=block_index,
                        _tool_summary=summary,
                        _tool_name=block.name,
                        _tid=block.call_id,
                    )
                )
                if block.input:
                    nodes.append(
                        _node(
                            "tool_call",
                            _emit_dict(block.input).split("\n"),
                            searchable=True,
                            _sec=section,
                            _blk=block_index,
                        )
                    )
                elif block.raw_text:
                    nodes.append(
                        _node(
                            "tool_call",
                            _sanitize(block.raw_text).split("\n"),
                            searchable=True,
                            _sec=section,
                            _blk=block_index,
                        )
                    )
                nodes.append(
                    _node(
                        "meta",
                        ["<<<tool_call"],
                        _sec=section,
                        _blk=block_index,
                    )
                )
                block_index += 1
                has_any = True
                continue

            if isinstance(block, Attachment) and block.note is None:
                has_any = emit_attachment(block, text_type) or has_any
        return has_any

    def emit_message_section(
        message: Message, event_metadata: Mapping[str, object]
    ) -> None:
        nonlocal section, block_index
        role = message.role
        custom_type = None
        if role == "developer":
            role = "system"
            label = "[system]"
        elif role.startswith("custom:"):
            custom_type = role.removeprefix("custom:")
            label = f"[custom] {custom_type}"
        else:
            label = f"[{role}]"
        node_type = role if role in {"system", "user", "assistant"} else "custom"
        regular_blocks = tuple(
            block
            for block in message.blocks
            if not isinstance(block, ToolResult)
            and not (isinstance(block, Attachment) and block.note is not None)
        )
        details = event_metadata.get("details")
        has_details = isinstance(details, Mapping) and bool(details)
        has_tool_results = any(isinstance(block, ToolResult) for block in message.blocks)
        empty_custom = (
            custom_type is not None
            and not regular_blocks
            and not has_details
        )
        if regular_blocks or empty_custom or (has_details and not has_tool_results):
            mark = len(nodes)
            saved_counter = data_counter[0]
            saved_block = block_index
            emit_separator()
            emit_header(label, role)
            has_any = emit_blocks(regular_blocks, node_type)
            if empty_custom:
                nodes.append(
                    _node(
                        node_type,
                        [f"[{custom_type}]"],
                        searchable=True,
                        _sec=section,
                        _blk=block_index,
                    )
                )
                block_index += 1
                has_any = True
            if has_details and not has_tool_results:
                rendered_details = {"details": details} if has_any else details
                nodes.append(
                    _node(
                        node_type,
                        _emit_dict(rendered_details).split("\n"),
                        searchable=True,
                        _sec=section,
                        _blk=block_index,
                    )
                )
                block_index += 1
                has_any = True
            if has_any:
                section += 1
            else:
                del nodes[mark:]
                data_counter[0] = saved_counter
                block_index = saved_block

        index = 0
        blocks = message.blocks
        while index < len(blocks):
            current = blocks[index]
            if not isinstance(current, ToolResult):
                index += 1
                continue

            call_id = current.call_id
            name = current.name
            is_error = current.is_error
            parts = []
            while index < len(blocks):
                item = blocks[index]
                if isinstance(item, ToolResult):
                    if item.call_id != call_id:
                        break
                    if item.text:
                        parts.append(_preprocess_tool_text(item.text, name))
                    is_error = is_error or item.is_error
                    index += 1
                    continue
                if isinstance(item, Attachment) and item.note == call_id:
                    filename = None
                    if extract_attachments:
                        filename = _extract_attachment(
                            item, outdir, data_prefix, data_counter
                        )
                    if item.kind == "image":
                        parts.append(f"[image: {filename}]" if filename else "[image]")
                    elif item.kind == "document":
                        parts.append(
                            f"[document: {filename}]" if filename else "[document]"
                        )
                    else:
                        parts.append(f"[{item.kind}]")
                    index += 1
                    continue
                break

            result_role = "tool_error" if is_error else "tool"
            result_type = "tool_error" if is_error else "tool_result"
            emit_separator()
            emit_header(
                f"[{result_role}] {name}:{_short_tid(call_id)}",
                result_role,
                kind="tool_result",
                _tid=call_id,
            )
            text = _sanitize("\n\n".join(parts))
            if has_details:
                detail_text = _emit_dict(details)
                if text:
                    indented = "\n".join(f"  {line}" for line in detail_text.split("\n"))
                    text = f"{text}\n\ndetails:\n{indented}"
                else:
                    text = detail_text
            nodes.append(
                _node(
                    result_type,
                    text.split("\n"),
                    searchable=True,
                    _sec=section,
                    _blk=block_index,
                )
            )
            block_index += 1
            section += 1

    for event_index, event in enumerate(segment.events):
        event_metadata = metadata_for_event(event_index)
        if isinstance(event, SystemEvent):
            if event.kind == "compact_summary":
                line_count = event.text.count("\n") + 1 if event.text else 0
                emit_separator()
                emit_header("[user]", "user", kind=event.kind)
                nodes.append(
                    _node(
                        "user",
                        [f"[compact summary — {line_count} lines]"],
                        searchable=False,
                        _sec=section,
                        _blk=block_index,
                    )
                )
                block_index += 1
                section += 1
                continue

            label = f"[{event.kind}]"
            node_type = "system_event"
            searchable = True
            if event.kind == "session_init":
                label = "[session_init]"
                node_type = "session_init"
                fields = event_metadata.get("fields")
                if isinstance(fields, Mapping) and fields:
                    lines = _emit_dict(fields).split("\n")
                    searchable = False
                else:
                    lines = _omp_session_init_lines(event_metadata)
                    if not lines:
                        text = _sanitize(event.text)
                        lines = text.split("\n") if text else []
            elif event.kind in {
                "model_change",
                "thinking_level_change",
                "service_tier_change",
                "mode_change",
                "label",
                "custom",
                "ttsr_injection",
                "mcp_tool_selection",
            }:
                node_type = "meta_event"
                searchable = False
                lines = (
                    _emit_dict(event_metadata).split("\n")
                    if event_metadata
                    else []
                )
            elif event.kind == "branch":
                label = "[branch]"
                node_type = "branch"
                lines = _omp_branch_lines(event, event_metadata)
            elif event.kind == "compaction":
                label = "[compaction]"
                node_type = "compaction"
                text = _sanitize(event.text)
                lines = text.split("\n") if text else []
            elif event.kind == "file_mention":
                label = "[file]"
                node_type = "file"
                lines = _omp_file_mention_lines(event_metadata)
            elif event.kind == "bash_execution":
                label = "[bash]"
                node_type = "bash"
                lines = _omp_execution_lines(event_metadata, "command")
            elif event.kind == "python_execution":
                label = "[python]"
                node_type = "python"
                lines = _omp_execution_lines(event_metadata, "code")
            else:
                text = _sanitize(event.text)
                lines = text.split("\n") if text else []

            if lines:
                emit_separator()
                emit_header(label, "system", kind=event.kind)
                nodes.append(
                    _node(
                        node_type,
                        lines,
                        searchable=searchable,
                        _sec=section,
                        _blk=block_index,
                    )
                )
                block_index += 1
                section += 1
            continue

        if isinstance(event, Message):
            emit_message_section(event, event_metadata)

    nodes.append(_node("meta", [""]))
    return nodes
