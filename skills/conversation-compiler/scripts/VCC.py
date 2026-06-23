#!/usr/bin/env python3
"""View-oriented Conversation Compiler (VCC) internals for Codex and OMP JSONL logs.

Produces per conversation chain:
  .txt       Full transcript (lossless rendering of JSONL)
  .min.txt   Brief mode (tool_call summaries with line refs, no separators)
  .view.txt  View mode (search-focused, written by xcc search)

Usage:
  uv --project /home/crosstyan/Code/VCC run xcc compile conversation.jsonl
  uv --project /home/crosstyan/Code/VCC run xcc search "kw" --harness all
"""

import argparse
import base64
import io
import json
import os
import re
import sys
import glob as globmod
from typing import Any

# ── dict emitter ──

def _emit_dict(data, indent=0):
    parts = []
    prefix = " " * indent
    for k, v in data.items():
        if isinstance(v, dict):
            parts.append(prefix + k + ":")
            parts.append(_emit_dict(v, indent + 2))
        elif isinstance(v, list):
            parts.append(prefix + k + ":")
            for item in v:
                if isinstance(item, dict):
                    parts.append(prefix + "  -")
                    parts.append(_emit_dict(item, indent + 4))
                else:
                    parts.append(prefix + "  - " + str(item))
        else:
            s = str(v)
            if "\n" in s:
                parts.append(prefix + k + ": |")
                p = " " * (indent + 2)
                for line in s.split("\n"):
                    parts.append((p + line) if line else "")
            else:
                parts.append(prefix + k + ": " + s)
    return "\n".join(parts)

# ── tokenizer ──

_TOK_RE = re.compile(
    r'[a-zA-Z]+'           # letters (grouped)
    r'|[0-9]+'             # digits (grouped)
    r'|[^\sa-zA-Z0-9]'     # single char: any non-whitespace non-letter non-digit
    r'|\s+'                # whitespace (preserved, not counted)
)
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_DATA_URL_RE = re.compile(r"data:([A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]{256,})")
_LONG_B64_RE = re.compile(r"(?<![A-Za-z0-9+/=])([A-Za-z0-9+/=]{1024,})(?![A-Za-z0-9+/=])")

def _tokenize(text):
    return _TOK_RE.findall(text)

# ── truncation (token-based) ──

def _trunc(text, limit, ref=""):
    if not limit or not text:
        return text
    tokens = _tokenize(text)
    count, cut = 0, len(tokens)
    for i, t in enumerate(tokens):
        if t.strip():
            count += 1
            if count > limit:
                cut = i
                break
    if cut >= len(tokens):
        return text
    return "".join(tokens[:cut]) + (f"...(truncated from {ref})" if ref else "...(truncated)")

def _looks_like_base64_blob(token):
    if len(token) < 1024:
        return False
    stripped = token.rstrip("=")
    if not stripped:
        return False
    # Avoid treating a long prose word or repeated identifier as a blob.
    kinds = sum(
        bool(re.search(pattern, stripped))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[+/]")
    )
    return kinds >= 3

def _redact_search_blob_line(line):
    """Bound grep/view lines by eliding embedded base64-like payloads."""
    def _data_url_repl(match):
        media_type = match.group(1)
        payload = re.sub(r"\s+", "", match.group(2))
        return f"data:{media_type};base64,[omitted {len(payload)} base64 chars]"

    def _b64_repl(match):
        token = match.group(1)
        if not _looks_like_base64_blob(token):
            return token
        return f"[omitted {len(token)} base64-like chars]"

    line = _DATA_URL_RE.sub(_data_url_repl, line)
    line = _LONG_B64_RE.sub(_b64_repl, line)
    return line

# ── match lines ──

def match_lines(lines, regex, ref_fn="x.txt", start_line=1):
    if not lines:
        return []
    end_line = start_line + len(lines) - 1
    from_ref = f"...(from {ref_fn}:{start_line}-{end_line})"

    matched = []
    for i, line in enumerate(lines):
        if regex.search(line):
            matched.append((start_line + i, line))
    if not matched:
        return [from_ref]

    block_ref = f"({ref_fn}:{start_line}-{end_line})"
    result = [block_ref]
    for ln, lt in matched:
        result.append(f"  {ln}: {_redact_search_blob_line(lt)}")
    return result


# ── lexer ──

SEP = "══════════════════════════════"
_DISCARD_T = {"queue-operation", "file-history-snapshot", "last-prompt", "progress"}
_DISCARD_S = {"stop_hook_summary", "api_error", "bridge_status", "informational", "local_command"}

def _short(fn):
    n, e = os.path.splitext(fn)
    return ("#" + n[-6:] + e) if len(n) > 12 else fn

def _short_tid(tid):
    return tid[-6:] if len(tid) > 6 else tid

def lex(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def detect_format(recs):
    for r in recs:
        rt = r.get("type")
        if rt in {"t3_session", "t3_message", "t3_custom_message", "t3_activity"}:
            return "t3code"
        if rt == "session":
            return "omp"
        if rt in {"session_meta", "response_item", "event_msg", "turn_context", "compacted"}:
            return "codex"
        if rt in {"assistant", "user", "system"}:
            return "claude"
    return "claude"


def _collect_stats(chain):
    """Extract usage/timing/model stats from a chain of records, including subagents."""
    from collections import defaultdict
    from datetime import datetime
    totals = defaultdict(int)
    models = set()
    timestamps = []
    api_calls = 0
    tool_uses = 0
    subagent_tokens = 0
    for r in chain:
        msg = r.get("message", {})
        ts = r.get("timestamp")
        if ts:
            timestamps.append(ts)
        usage = msg.get("usage")
        if usage:
            api_calls += 1
            for k, v in usage.items():
                if isinstance(v, int):
                    totals[k] += v
        model = msg.get("model")
        if model:
            models.add(model)
        content = msg.get("content", [])
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_uses += 1
        # subagent usage from toolUseResult
        tur = r.get("toolUseResult")
        if isinstance(tur, dict) and tur.get("agentId"):
            try:
                subagent_tokens += int(tur.get("totalTokens", 0))
            except (ValueError, TypeError):
                pass
    duration = None
    if len(timestamps) >= 2:
        try:
            t0 = datetime.fromisoformat(min(timestamps).replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(max(timestamps).replace("Z", "+00:00"))
            duration = int((t1 - t0).total_seconds())
        except Exception:
            pass
    if api_calls == 0:
        return None
    lines = [SEP, "[stats]", ""]
    lines.append(f"model: {', '.join(sorted(models))}")
    lines.append(f"api_calls: {api_calls}  tool_uses: {tool_uses}")
    if duration is not None:
        m, s = divmod(duration, 60)
        lines.append(f"duration: {m}m{s:02d}s" if m else f"duration: {s}s")
    inp = totals.get("input_tokens", 0)
    cr = totals.get("cache_read_input_tokens", 0)
    cc = totals.get("cache_creation_input_tokens", 0)
    out = totals.get("output_tokens", 0)
    own = inp + cr + cc + out
    own_eff = int(inp * 1.0 + cr * 0.1 + cc * 1.25 + out * 5.0)

    def _fmt_block(label, raw, eff):
        return f"{label}: {raw:,} (effective: {eff:,})"

    if subagent_tokens:
        lines.append("")
        lines.append("Subagents:")
        lines.append(f"  total: {subagent_tokens:,}")
        lines.append("")
        lines.append("Main:")

    parts = []
    if inp: parts.append(f"input: {inp:,}")
    if cr: parts.append(f"cache_read: {cr:,}")
    if cc: parts.append(f"cache_create: {cc:,}")
    pfx = "  " if subagent_tokens else ""
    if parts:
        lines.append(f"{pfx}{'  '.join(parts)}")
    lines.append(f"{pfx}output: {out:,}")
    lines.append(f"{pfx}{_fmt_block('total', own, own_eff)}")

    if subagent_tokens:
        all_raw = own + subagent_tokens
        all_eff = own_eff + subagent_tokens
        lines.append("")
        lines.append("All:")
        lines.append(f"  {_fmt_block('total', all_raw, all_eff)}")
    return lines

def _sanitize(text):
    if not text:
        return text
    if "\r" in text:
        text = text.replace("\r", "")
    if "\x1b" in text:
        text = _ANSI_RE.sub("", text)
    return _CTRL_RE.sub("", text)

def _preprocess_tool_text(text, tool_name):
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

def _discard(r):
    t = r.get("type")
    return t in _DISCARD_T or (t == "system" and r.get("subtype") in _DISCARD_S)

def merge_chunks(recs):
    merged = []
    active_mid = None
    active_idx = None
    for r in recs:
        if r.get("type") == "assistant":
            m = r.get("message", {})
            mid = m.get("id")
            if mid and mid == active_mid and active_idx is not None:
                merged[active_idx]["message"]["content"].extend(m.get("content", []))
                if m.get("stop_reason"):
                    merged[active_idx]["message"]["stop_reason"] = m["stop_reason"]
            else:
                merged.append(r)
                if mid:
                    active_mid = mid
                    active_idx = len(merged) - 1
                else:
                    active_mid = None
                    active_idx = None
        else:
            merged.append(r)
            if not _discard(r):
                active_mid = None
                active_idx = None
    return merged

def split_chains(recs):
    kept = [r for r in recs if not _discard(r)]
    chains, cur = [], []
    for r in kept:
        if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
            if cur: chains.append(cur)
            cur = []
        else:
            cur.append(r)
    if cur: chains.append(cur)
    return chains


def split_codex_chains(recs):
    chains = []
    cur = []
    for r in recs:
        rt = r.get("type")
        if rt == "compacted":
            if cur:
                chains.append(cur)
            cur = []
            continue
        if rt == "event_msg" and r.get("payload", {}).get("type") == "context_compacted":
            continue
        cur.append(r)
    if cur:
        chains.append(cur)
    return chains


def split_omp_chains(recs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chains = []
    cur = []
    for r in recs:
        if r.get("type") == "compaction":
            if cur:
                chains.append(cur)
            cur = [r]
            continue
        cur.append(r)
    if cur:
        chains.append(cur)
    return chains


def split_t3code_chains(recs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [recs] if recs else []

# ── image / doc ──

def _media_ext(media_type, default_ext):
    if "/" not in media_type:
        return default_ext
    ext = media_type.split("/", 1)[1].split("+", 1)[0]
    if ext == "jpeg":
        return "jpg"
    return ext or default_ext

def _extract_base64(source, outdir, data_prefix, data_index, stem, default_mt, default_ext):
    mt = source.get("media_type", default_mt)
    fn = f"{data_prefix}_{stem}_{data_index}.{_media_ext(mt, default_ext)}"
    with open(os.path.join(outdir, fn), "wb") as f:
        f.write(base64.b64decode(source.get("data", "")))
    return fn

def _extract_img(source, outdir, data_prefix, data_index):
    return _extract_base64(source, outdir, data_prefix, data_index,
                           "img", "image/png", "png")

def _extract_doc(source, outdir, data_prefix, data_index):
    return _extract_base64(source, outdir, data_prefix, data_index,
                           "doc", "application/octet-stream", "bin")


# ── tool_call summary ──

_TOOL_SUMMARY_FIELDS = {
    "Read": "file_path", "Edit": "file_path", "Write": "file_path",
    "Glob": "pattern", "Grep": "pattern",
    "Agent": "description", "Skill": "skill",
    "read": "path", "edit": "path", "write": "path",
    "find": "pattern", "search": "pattern", "web_search": "query",
    "task": "description", "skill": "skill",
    "exec_command": "cmd", "write_stdin": "chars",
    "spawn_agent": "message", "send_input": "message",
    "wait_agent": "target", "close_agent": "target",
    "view_image": "path", "request_user_input": "questions",
}

def _tool_summary(name, inp):
    """Build one-line summary: * Name "param" """
    field = _TOOL_SUMMARY_FIELDS.get(name)
    if field and field in inp:
        return f'* {name} "{inp[field]}"'
    if name == "Bash":
        val = inp.get("description") or inp.get("command", "")
        if val:
            # truncate long commands
            if not inp.get("description") and len(val) > 60:
                val = val[:57] + "..."
            return f'* {name} "{val}"'
    if name == "bash":
        val = inp.get("description") or inp.get("command", "")
        if val:
            if not inp.get("description") and len(val) > 60:
                val = val[:57] + "..."
            return f'* {name} "{val}"'
    return f"* {name}"


# ── IR node ──

def _node(typ, content, **kw):
    o = {"type": typ, "content": content,
         "searchable": kw.pop("searchable", False)}
    o.update(kw)
    return o

# ── parser ──

def parse(chain, outdir, data_prefix, data_ctr):
    ir = []
    sec = 0
    blk = 0

    tid_name = {}
    for r in chain:
        if r.get("type") == "assistant":
            for b in r.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    tid_name[b.get("id", "")] = b.get("name", "unknown")

    def _emit_sep():
        if sec > 0:
            ir.append(_node("meta", ["", SEP]))

    def _emit_header(h):
        ir.append(_node("meta_header", [h, ""], _sec=sec))

    def _emit_blocks(blocks, text_type):
        nonlocal blk
        has_any = False
        for b in blocks:
            bt = b.get("type")
            if bt == "thinking":
                txt = _sanitize(b.get("thinking", ""))
                if not txt: continue
                ir.append(_node("meta", [">>>thinking"], _sec=sec, _blk=blk))
                ir.append(_node("thinking", txt.split("\n"), searchable=True,
                                 _sec=sec, _blk=blk))
                ir.append(_node("meta", ["<<<thinking"], _sec=sec, _blk=blk))
                blk += 1; has_any = True

            elif bt == "redacted_thinking":
                ir.append(_node("meta", [">>>redacted_thinking"], _sec=sec, _blk=blk))
                ir.append(_node("redacted_thinking",
                                 ["[content redacted by model provider]"],
                                 searchable=True, _sec=sec, _blk=blk))
                ir.append(_node("meta", ["<<<redacted_thinking"], _sec=sec, _blk=blk))
                blk += 1; has_any = True

            elif bt == "text":
                txt = _sanitize(b.get("text", ""))
                if not txt: continue
                ir.append(_node(text_type, txt.split("\n"), searchable=True,
                                 _sec=sec, _blk=blk))
                blk += 1; has_any = True

            elif bt == "tool_use":
                name = b.get("name", "unknown")
                tid = b.get("id", "")
                inp = b.get("input", {})
                hl = f">>>tool_call {name}:{_short_tid(tid)}"
                summary = _tool_summary(name, inp)
                ir.append(_node("meta", [hl], _sec=sec, _blk=blk,
                                 _tool_summary=summary))
                if inp:
                    ir.append(_node("tool_call", _emit_dict(inp).split("\n"),
                                     searchable=True, _sec=sec, _blk=blk))
                ir.append(_node("meta", ["<<<tool_call"], _sec=sec, _blk=blk))
                blk += 1; has_any = True

            elif bt == "image":
                src = b.get("source", {})
                if src.get("type") == "base64":
                    fn = _extract_img(src, outdir, data_prefix, data_ctr[0])
                    data_ctr[0] += 1
                    ir.append(_node(f"{text_type}_image", [f"[image: {fn}]"],
                                     searchable=True, _sec=sec, _blk=blk))
                    blk += 1; has_any = True

            elif bt == "document":
                src = b.get("source", {})
                label = "[document]"
                if src.get("type") == "base64":
                    fn = _extract_doc(src, outdir, data_prefix, data_ctr[0])
                    data_ctr[0] += 1
                    label = f"[document: {fn}]"
                ir.append(_node(f"{text_type}_document", [label],
                                 searchable=True, _sec=sec, _blk=blk))
                blk += 1; has_any = True
        return has_any

    for r in chain:
        rt = r.get("type")

        if rt == "system":
            if r.get("subtype") == "compact_boundary": continue
            content = r.get("content", "") or r.get("message", {}).get("content", "")
            if not content: continue
            _emit_sep(); _emit_header("[system]")
            if isinstance(content, list):
                _emit_blocks(content, "system")
            else:
                ir.append(_node("system", _sanitize(content).split("\n"), searchable=True,
                                 _sec=sec, _blk=blk))
                blk += 1
            sec += 1

        elif rt == "user":
            if r.get("isCompactSummary"):
                content = r.get("message", {}).get("content", "")
                nlines = content.count("\n") + 1 if content else 0
                _emit_sep(); _emit_header("[user]")
                ir.append(_node("user", [f"[compact summary — {nlines} lines]"], searchable=False,
                                 _sec=sec, _blk=blk))
                blk += 1; sec += 1
                continue
            content = r.get("message", {}).get("content", "")
            if isinstance(content, str):
                if content:
                    _emit_sep(); _emit_header("[user]")
                    ir.append(_node("user", _sanitize(content).split("\n"), searchable=True,
                                     _sec=sec, _blk=blk))
                    blk += 1; sec += 1
            elif isinstance(content, list):
                tblocks = [b for b in content if b.get("type") != "tool_result"]
                tresults = [b for b in content if b.get("type") == "tool_result"]
                if tblocks:
                    mark = len(ir)
                    saved_data = data_ctr[0]
                    saved_blk = blk
                    _emit_sep(); _emit_header("[user]")
                    if _emit_blocks(tblocks, "user"):
                        sec += 1
                    else:
                        del ir[mark:]
                        data_ctr[0] = saved_data
                        blk = saved_blk
                for tr in tresults:
                    tuid = tr.get("tool_use_id", "")
                    is_err = tr.get("is_error", False)
                    nm = tid_name.get(tuid, "unknown")
                    role = "tool_error" if is_err else "tool"
                    btype = "tool_error" if is_err else "tool_result"
                    _emit_sep(); _emit_header(f"[{role}] {nm}:{_short_tid(tuid)}")
                    tc = tr.get("content", "")
                    parts = []
                    if isinstance(tc, str):
                        parts.append(_preprocess_tool_text(tc, nm))
                    elif isinstance(tc, list):
                        for item in tc:
                            if item.get("type") == "text":
                                parts.append(_preprocess_tool_text(item.get("text", ""), nm))
                            elif item.get("type") == "image":
                                src = item.get("source", {})
                                if src.get("type") == "base64":
                                    fn = _extract_img(src, outdir, data_prefix, data_ctr[0])
                                    data_ctr[0] += 1
                                    parts.append(f"[image: {fn}]")
                            elif item.get("type") == "document":
                                src = item.get("source", {})
                                if src.get("type") == "base64":
                                    fn = _extract_doc(src, outdir, data_prefix, data_ctr[0])
                                    data_ctr[0] += 1
                                    parts.append(f"[document: {fn}]")
                    ir.append(_node(btype, _sanitize("\n\n".join(parts)).split("\n"),
                                     searchable=True, _sec=sec, _blk=blk))
                    blk += 1; sec += 1

        elif rt == "assistant":
            blocks = r.get("message", {}).get("content", [])
            has = any(
                (b.get("type") == "thinking" and b.get("thinking")) or
                b.get("type") == "redacted_thinking" or
                (b.get("type") == "text" and b.get("text")) or
                b.get("type") == "tool_use" or
                b.get("type") == "image" or
                b.get("type") == "document"
                for b in blocks)
            if has:
                _emit_sep(); _emit_header("[assistant]")
                _emit_blocks(blocks, "assistant")
                sec += 1

    ir.append(_node("meta", [""]))  # trailing newline
    return ir


def _render_json_lines(value):
    return json.dumps(value, ensure_ascii=False, indent=2).split("\n")


def _maybe_json_string(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _structured_lines(value):
    if value in (None, "", {}, []):
        return []
    if isinstance(value, dict):
        return _emit_dict(value).split("\n")
    if isinstance(value, list):
        return _render_json_lines(value)
    parsed = _maybe_json_string(value)
    if isinstance(parsed, dict):
        return _emit_dict(parsed).split("\n")
    if isinstance(parsed, list):
        return _render_json_lines(parsed)
    return _sanitize(str(value)).split("\n")


def _codex_content_label(item):
    item_type = item.get("type", "content")
    if item_type in {"input_image", "output_image", "image"}:
        return "[image]"
    if item_type in {"input_file", "output_file", "file", "document"}:
        return "[document]"
    return f"[{item_type}]"


def _codex_message_lines(content):
    parts = []
    for item in content or []:
        item_type = item.get("type")
        if item_type in {"input_text", "output_text", "text"}:
            text = _sanitize(item.get("text", ""))
            if text:
                parts.append(text)
            continue
        if item_type == "refusal":
            text = _sanitize(item.get("refusal", ""))
            if text:
                parts.append(text)
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(_sanitize(text))
            continue
        parts.append(_codex_content_label(item))
    if not parts:
        return []
    return _sanitize("\n\n".join(parts)).split("\n")


def _codex_tool_call_name(payload):
    pt = payload.get("type")
    if pt == "function_call":
        return payload.get("name", "unknown")
    if pt == "custom_tool_call":
        return (
            payload.get("name")
            or payload.get("call_name")
            or payload.get("raw_name")
            or "custom_tool"
        )
    if pt == "web_search_call":
        return "web_search"
    if pt == "image_generation_call":
        return "image_generation"
    if pt == "local_shell_call":
        return "local_shell"
    if pt == "tool_search_call":
        return "tool_search"
    return payload.get("name", pt or "unknown")


def _codex_tool_call_id(payload):
    return (
        payload.get("call_id")
        or payload.get("id")
        or payload.get("tool_call_id")
        or ""
    )


def _codex_tool_input(payload):
    pt = payload.get("type")
    if pt == "function_call":
        parsed = _maybe_json_string(payload.get("arguments"))
        if isinstance(parsed, dict):
            return parsed
        raw = payload.get("arguments")
        return {"arguments": raw} if raw else {}
    if pt == "custom_tool_call":
        if isinstance(payload.get("input"), dict):
            return payload.get("input")
        parsed = _maybe_json_string(payload.get("input"))
        if isinstance(parsed, dict):
            return parsed
        raw = payload.get("input")
        return {"input": raw} if raw else {}
    if pt == "web_search_call":
        query = payload.get("query")
        if query:
            return {"query": query}
        return {}
    if pt == "image_generation_call":
        prompt = payload.get("prompt") or payload.get("revised_prompt")
        if prompt:
            return {"prompt": prompt}
        return {}
    if pt == "local_shell_call":
        command = payload.get("command")
        if command:
            return {"command": command}
        return {}
    if pt == "tool_search_call":
        query = payload.get("query")
        if query:
            return {"query": query}
        return {}
    return {}


def _codex_tool_output_lines(payload):
    pt = payload.get("type")
    if pt in {"function_call_output", "custom_tool_call_output"}:
        return _structured_lines(payload.get("output"))
    if pt == "tool_search_output":
        return _structured_lines(payload.get("output"))
    return []


def parse_codex(chain, outdir, data_prefix, data_ctr):
    del outdir, data_prefix, data_ctr
    ir = []
    sec = 0
    blk = 0

    call_names = {}
    for r in chain:
        if r.get("type") != "response_item":
            continue
        payload = r.get("payload", {})
        pt = payload.get("type")
        if pt in {
            "function_call",
            "custom_tool_call",
            "web_search_call",
            "image_generation_call",
            "local_shell_call",
            "tool_search_call",
        }:
            call_id = _codex_tool_call_id(payload)
            if call_id:
                call_names[call_id] = _codex_tool_call_name(payload)

    def _emit_sep():
        if sec > 0:
            ir.append(_node("meta", ["", SEP]))

    def _emit_header(h):
        ir.append(_node("meta_header", [h, ""], _sec=sec))

    for r in chain:
        rt = r.get("type")
        if rt in {"session_meta", "turn_context", "compacted"}:
            continue

        if rt == "response_item":
            payload = r.get("payload", {})
            pt = payload.get("type")

            if pt == "reasoning":
                continue

            if pt == "message":
                role = payload.get("role")
                lines = _codex_message_lines(payload.get("content", []))
                if not lines:
                    continue
                if role == "developer":
                    label = "[system]"
                    node_type = "system"
                elif role == "user":
                    label = "[user]"
                    node_type = "user"
                else:
                    label = "[assistant]"
                    node_type = "assistant"
                _emit_sep()
                _emit_header(label)
                ir.append(_node(node_type, lines, searchable=True, _sec=sec, _blk=blk))
                blk += 1
                sec += 1
                continue

            if pt in {
                "function_call",
                "custom_tool_call",
                "web_search_call",
                "image_generation_call",
                "local_shell_call",
                "tool_search_call",
            }:
                name = _codex_tool_call_name(payload)
                call_id = _codex_tool_call_id(payload)
                tool_input = _codex_tool_input(payload)
                summary = _tool_summary(name, tool_input)
                _emit_sep()
                _emit_header("[assistant]")
                ir.append(
                    _node(
                        "meta",
                        [f">>>tool_call {name}:{_short_tid(call_id)}"],
                        _sec=sec,
                        _blk=blk,
                        _tool_summary=summary,
                    )
                )
                lines = _structured_lines(tool_input)
                if lines:
                    ir.append(
                        _node("tool_call", lines, searchable=True, _sec=sec, _blk=blk)
                    )
                ir.append(_node("meta", ["<<<tool_call"], _sec=sec, _blk=blk))
                blk += 1
                sec += 1
                continue

            if pt in {"function_call_output", "custom_tool_call_output", "tool_search_output"}:
                call_id = payload.get("call_id", "")
                name = call_names.get(call_id, "unknown")
                lines = _codex_tool_output_lines(payload)
                if not lines:
                    continue
                _emit_sep()
                _emit_header(f"[tool] {name}:{_short_tid(call_id)}")
                ir.append(
                    _node("tool_result", lines, searchable=True, _sec=sec, _blk=blk)
                )
                blk += 1
                sec += 1
                continue

            continue

        if rt == "event_msg":
            payload = r.get("payload", {})
            et = payload.get("type")
            if et == "web_search_end":
                call_id = payload.get("call_id", "")
                lines = _structured_lines(
                    {
                        "query": payload.get("query", ""),
                        "action": payload.get("action"),
                    }
                )
                if not lines:
                    continue
                _emit_sep()
                _emit_header(f"[tool] web_search:{_short_tid(call_id)}")
                ir.append(
                    _node("tool_result", lines, searchable=True, _sec=sec, _blk=blk)
                )
                blk += 1
                sec += 1

    ir.append(_node("meta", [""]))
    return ir


def _omp_content_lines(content: object) -> list[str]:
    if isinstance(content, str):
        text = _sanitize(content)
        return text.split("\n") if text else []
    if not isinstance(content, list):
        return []
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text = _sanitize(item.get("text", ""))
            if text:
                parts.append(text)
        elif item_type == "image":
            parts.append("[image]")
        elif item_type in {"document", "file"}:
            parts.append("[document]")
        elif isinstance(item.get("text"), str):
            parts.append(_sanitize(item.get("text")))
        elif item_type:
            parts.append(f"[{item_type}]")
    if not parts:
        return []
    return _sanitize("\n\n".join(parts)).split("\n")


def _omp_tool_result_lines(message: dict[str, Any]) -> list[str]:
    lines = _omp_content_lines(message.get("content"))
    details = message.get("details")
    if isinstance(details, dict) and details:
        detail_lines = _structured_lines(details)
        if lines:
            return lines + ["", "details:"] + [f"  {line}" for line in detail_lines]
        return detail_lines
    return lines


def _omp_custom_lines(message: dict[str, Any]) -> list[str]:
    custom_type = message.get("customType") or message.get("role") or "custom"
    lines = _omp_content_lines(message.get("content"))
    details = message.get("details")
    if isinstance(details, dict) and details:
        detail_lines = _structured_lines(details)
        if lines:
            return lines + ["", "details:"] + [f"  {line}" for line in detail_lines]
        return detail_lines
    return lines or [f"[{custom_type}]"]


def _omp_file_mention_lines(message: dict[str, Any]) -> list[str]:
    lines = []
    for item in message.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = item.get("path") or "(unknown)"
        lines.append(f"<file path=\"{path}\">")
        content = item.get("content")
        if isinstance(content, str) and content:
            lines.extend(_sanitize(content).split("\n"))
        elif item.get("image"):
            lines.append("[image]")
        lines.append("</file>")
    return lines


def _omp_execution_lines(message: dict[str, Any], source_key: str) -> list[str]:
    source = message.get(source_key) or ""
    output = message.get("output") or ""
    lines = []
    if source:
        lines.append(f"{source_key}: |")
        lines.extend(f"  {line}" for line in _sanitize(source).split("\n"))
    if "exitCode" in message:
        lines.append(f"exit_code: {message.get('exitCode')}")
    if message.get("cancelled"):
        lines.append("cancelled: true")
    if output:
        lines.append("output: |")
        lines.extend(f"  {line}" for line in _sanitize(output).split("\n"))
    return lines


def _omp_session_init_lines(entry: dict[str, Any]) -> list[str]:
    lines = []
    system_prompt = entry.get("systemPrompt")
    task = entry.get("task")
    tools = entry.get("tools")
    if isinstance(system_prompt, str) and system_prompt:
        lines.append("system_prompt: |")
        lines.extend(f"  {line}" for line in _sanitize(system_prompt).split("\n"))
    if isinstance(task, str) and task:
        lines.append("task: |")
        lines.extend(f"  {line}" for line in _sanitize(task).split("\n"))
    if isinstance(tools, list) and tools:
        lines.append("tools:")
        for tool in tools:
            lines.append(f"  - {_sanitize(str(tool))}")
    return lines


def _omp_entry_field_lines(entry: dict[str, Any], fields: list[str]) -> list[str]:
    data = {field: entry[field] for field in fields if field in entry}
    return _structured_lines(data) if data else []


def parse_omp(
    chain: list[dict[str, Any]],
    outdir: str,
    data_prefix: str,
    data_ctr: list[int],
) -> list[dict[str, Any]]:
    del outdir, data_prefix, data_ctr
    ir = []
    sec = 0
    blk = 0

    def _emit_sep() -> None:
        if sec > 0:
            ir.append(_node("meta", ["", SEP]))

    def _emit_header(h: str) -> None:
        ir.append(_node("meta_header", [h, ""], _sec=sec))

    def _emit_text_section(label: str, node_type: str, lines: list[str], searchable: bool = True) -> None:
        nonlocal sec, blk
        if not lines:
            return
        _emit_sep()
        _emit_header(label)
        ir.append(_node(node_type, lines, searchable=searchable, _sec=sec, _blk=blk))
        blk += 1
        sec += 1

    for r in chain:
        rt = r.get("type")
        if rt == "session":
            continue

        if rt == "compaction":
            summary = r.get("summary") or r.get("shortSummary") or r.get("content")
            lines = _sanitize(summary).split("\n") if isinstance(summary, str) and summary else []
            _emit_text_section("[compaction]", "compaction", lines)
            continue

        if rt == "branch_summary":
            lines = _sanitize(r.get("summary", "")).split("\n") if r.get("summary") else []
            if r.get("fromId"):
                lines = [f"from: {r.get('fromId')}", ""] + lines
            _emit_text_section("[branch]", "branch", lines)
            continue

        if rt == "custom_message":
            custom_type = r.get("customType") or "custom_message"
            lines = _omp_content_lines(r.get("content"))
            details = r.get("details")
            if isinstance(details, dict) and details:
                detail_lines = _structured_lines(details)
                lines = (lines + ["", "details:"] + [f"  {line}" for line in detail_lines]) if lines else detail_lines
            _emit_text_section(f"[custom] {custom_type}", "custom", lines)
            continue

        if rt == "session_init":
            _emit_text_section("[session_init]", "session_init", _omp_session_init_lines(r))
            continue

        if rt in {"model_change", "thinking_level_change", "service_tier_change", "mode_change"}:
            lines = _omp_entry_field_lines(r, ["model", "role", "thinkingLevel", "configured", "serviceTier", "mode", "data"])
            _emit_text_section(f"[{rt}]", "meta_event", lines, searchable=False)
            continue

        if rt in {"label", "custom", "ttsr_injection", "mcp_tool_selection"}:
            lines = _omp_entry_field_lines(
                r,
                ["targetId", "label", "customType", "data", "injectedRules", "selectedToolNames"],
            )
            _emit_text_section(f"[{rt}]", "meta_event", lines, searchable=False)
            continue

        if rt != "message":
            continue

        message = r.get("message", {})
        if not isinstance(message, dict):
            continue
        role = message.get("role")

        if role in {"user", "developer"}:
            label = "[system]" if role == "developer" else "[user]"
            node_type = "system" if role == "developer" else "user"
            _emit_text_section(label, node_type, _omp_content_lines(message.get("content")))
            continue

        if role == "assistant":
            blocks = message.get("content") or []
            if not isinstance(blocks, list):
                _emit_text_section("[assistant]", "assistant", _omp_content_lines(blocks))
                continue

            mark = len(ir)
            saved_blk = blk
            _emit_sep()
            _emit_header("[assistant]")
            has_any = False
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text":
                    lines = _sanitize(block.get("text", "")).split("\n") if block.get("text") else []
                    if lines:
                        ir.append(_node("assistant", lines, searchable=True, _sec=sec, _blk=blk))
                        blk += 1
                        has_any = True
                    continue
                if bt == "thinking":
                    thinking = _sanitize(block.get("thinking", ""))
                    if thinking:
                        ir.append(_node("meta", [">>>thinking"], _sec=sec, _blk=blk))
                        ir.append(_node("thinking", thinking.split("\n"), searchable=True, _sec=sec, _blk=blk))
                        ir.append(_node("meta", ["<<<thinking"], _sec=sec, _blk=blk))
                        blk += 1
                        has_any = True
                    continue
                if bt == "redactedThinking":
                    ir.append(_node("meta", [">>>redacted_thinking"], _sec=sec, _blk=blk))
                    ir.append(
                        _node(
                            "redacted_thinking",
                            ["[content redacted by model provider]"],
                            searchable=True,
                            _sec=sec,
                            _blk=blk,
                        )
                    )
                    ir.append(_node("meta", ["<<<redacted_thinking"], _sec=sec, _blk=blk))
                    blk += 1
                    has_any = True
                    continue
                if bt == "toolCall":
                    name = block.get("name", "unknown")
                    call_id = block.get("id", "")
                    args = block.get("arguments")
                    if not isinstance(args, dict):
                        args = {}
                    summary = _tool_summary(name, args)
                    ir.append(
                        _node(
                            "meta",
                            [f">>>tool_call {name}:{_short_tid(call_id)}"],
                            _sec=sec,
                            _blk=blk,
                            _tool_summary=summary,
                        )
                    )
                    lines = _structured_lines(args)
                    if lines:
                        ir.append(_node("tool_call", lines, searchable=True, _sec=sec, _blk=blk))
                    ir.append(_node("meta", ["<<<tool_call"], _sec=sec, _blk=blk))
                    blk += 1
                    has_any = True
                    continue
                if bt == "image":
                    ir.append(_node("assistant_image", ["[image]"], searchable=True, _sec=sec, _blk=blk))
                    blk += 1
                    has_any = True

            if has_any:
                sec += 1
            else:
                del ir[mark:]
                blk = saved_blk
            continue

        if role == "toolResult":
            name = message.get("toolName", "unknown")
            call_id = message.get("toolCallId", "")
            label = "[tool_error]" if message.get("isError") else "[tool]"
            _emit_text_section(f"{label} {name}:{_short_tid(call_id)}", "tool_result", _omp_tool_result_lines(message))
            continue

        if role in {"custom", "hookMessage"}:
            custom_type = message.get("customType") or role
            _emit_text_section(f"[custom] {custom_type}", "custom", _omp_custom_lines(message))
            continue

        if role == "branchSummary":
            lines = _sanitize(message.get("summary", "")).split("\n") if message.get("summary") else []
            if message.get("fromId"):
                lines = [f"from: {message.get('fromId')}", ""] + lines
            _emit_text_section("[branch]", "branch", lines)
            continue

        if role == "compactionSummary":
            lines = _sanitize(message.get("summary", "")).split("\n") if message.get("summary") else []
            _emit_text_section("[compaction]", "compaction", lines)
            continue

        if role == "fileMention":
            _emit_text_section("[file]", "file", _omp_file_mention_lines(message))
            continue

        if role == "bashExecution":
            _emit_text_section("[bash]", "bash", _omp_execution_lines(message, "command"))
            continue

        if role == "pythonExecution":
            _emit_text_section("[python]", "python", _omp_execution_lines(message, "code"))
            continue

    ir.append(_node("meta", [""]))
    return ir


def parse_t3code(
    chain: list[dict[str, Any]],
    outdir: str,
    data_prefix: str,
    data_ctr: list[int],
) -> list[dict[str, Any]]:
    del outdir, data_prefix, data_ctr
    ir = []
    sec = 0
    blk = 0

    def _emit_sep() -> None:
        if sec > 0:
            ir.append(_node("meta", ["", SEP]))

    def _emit_header(h: str) -> None:
        ir.append(_node("meta_header", [h, ""], _sec=sec))

    def _emit_text_section(label: str, node_type: str, lines: list[str], searchable: bool = True) -> None:
        nonlocal sec, blk
        if not lines:
            return
        _emit_sep()
        _emit_header(label)
        ir.append(_node(node_type, lines, searchable=searchable, _sec=sec, _blk=blk))
        blk += 1
        sec += 1

    for r in chain:
        rt = r.get("type")

        if rt == "t3_session":
            lines = _structured_lines(
                {
                    key: r.get(key)
                    for key in ("id", "timestamp", "cwd", "title", "model")
                    if r.get(key) is not None
                }
            )
            _emit_text_section("[session_init]", "session_init", lines, searchable=False)
            continue

        if rt == "t3_message":
            role = r.get("role")
            content = r.get("content")
            lines = _sanitize(content).split("\n") if isinstance(content, str) and content else []
            if role == "developer":
                _emit_text_section("[system]", "system", lines)
            elif role == "user":
                _emit_text_section("[user]", "user", lines)
            elif role == "assistant":
                _emit_text_section("[assistant]", "assistant", lines)
            else:
                _emit_text_section(f"[custom] message:{role or 'unknown'}", "custom", lines)
            continue

        if rt in {"t3_custom_message", "t3_activity"}:
            custom_type = r.get("customType") or rt
            content = r.get("content")
            lines = _sanitize(content).split("\n") if isinstance(content, str) and content else []
            details = r.get("details")
            if isinstance(details, dict) and details:
                detail_lines = _structured_lines(details)
                lines = (lines + ["", "details:"] + [f"  {line}" for line in detail_lines]) if lines else detail_lines
            _emit_text_section(f"[custom] {custom_type}", "custom", lines)
            continue

    ir.append(_node("meta", [""]))
    return ir


# ── IR walk ──

def _is_tool_summary(o):
    return o.get("_tool_summary") is not None

def _walk(ir, key="content"):
    prev_blk = None
    prev_o = None
    for o in ir:
        c = o.get(key)
        if c is None:
            continue
        if not c:
            continue
        blk = o.get("_blk")
        if blk is not None and prev_blk is not None and blk != prev_blk:
            if key == "content":
                blank = True
            else:
                # non-content: suppress blank between consecutive tool summaries
                blank = not (_is_tool_summary(prev_o) and _is_tool_summary(o))
        else:
            blank = False
        yield o, c, blank
        if blk is not None:
            prev_blk = blk
            prev_o = o
        elif SEP in o.get("content", []):
            prev_blk = None
            prev_o = None


# ── line assignment ──

def assign_lines(ir):
    line = 0
    for o, c, blank in _walk(ir, "content"):
        if blank: line += 1
        o["start_line"] = line
        line += len(c)
        o["end_line"] = line - 1
    return line


# ── lowering helpers ──

def _is_truncatable(o):
    t = o["type"]
    if t in ("meta", "meta_header", "thinking", "redacted_thinking"):
        return False
    if t.endswith("_image") or t.endswith("_document"):
        return False
    return True

def _is_thinking(o):
    return o["type"] in ("thinking", "redacted_thinking")

def _sec_roles(ir):
    """Map sec -> role from meta_header content."""
    roles = {}
    for o in ir:
        if o["type"] == "meta_header":
            s = o.get("_sec")
            if s is None: continue
            h = o["content"][0]
            if h.startswith("[tool_error]"):
                roles[s] = "tool_error"
            elif h.startswith("[tool]"):
                roles[s] = "tool"
            elif h.startswith("[assistant]"):
                roles[s] = "assistant"
            elif h.startswith("[user]"):
                roles[s] = "user"
            elif h.startswith("[system]"):
                roles[s] = "system"
    return roles


# ── brief-mode filtering ──

_BRIEF_STRIP_RE = re.compile(
    r'<(ide_opened_file|ide_selection|system-reminder|command-message)>.*?</\1>\s*',
    re.DOTALL)
_BRIEF_UNWRAP_RE = re.compile(r'</?(?:command-name|command-args)>')
_META_USER_RE = re.compile(
    r'^\s*<(?:task-notification|local-command-caveat|local-command-stdout'
    r'|local-command-stderr|environment_context|user_instructions)\b')
_SKILL_USER_RE = re.compile(r'^Base directory for this skill:')
_CODEX_AGENTS_USER_RE = re.compile(r'^# AGENTS\.md instructions for ')
_BRIEF_HIDE_TOOLS = {"TodoWrite", "ToolSearch"}
_BRIEF_HIDE_EXACT = {"Continue from where you left off.", "No response requested."}

def _strip_noise_xml(text):
    """Strip known noise XML from user text for brief mode."""
    text = _BRIEF_STRIP_RE.sub('', text)
    text = _BRIEF_UNWRAP_RE.sub('', text)
    return text.strip()

def _user_hidden_in_brief(ir, sec):
    """Check if a user section should be entirely hidden in brief mode."""
    blocks = [o for o in ir if o.get("_sec") == sec and o.get("searchable")]
    if not blocks:
        return False
    for o in blocks:
        text = "\n".join(o["content"]).strip()
        if not text:
            continue
        if _META_USER_RE.match(text):
            continue
        if _SKILL_USER_RE.match(text):
            continue
        if _CODEX_AGENTS_USER_RE.match(text):
            continue
        if not _strip_noise_xml(text):
            continue
        return False
    return True

def _section_hidden_exact(ir, sec):
    """Check if all searchable content in a section is an exact-match hide string."""
    blocks = [o for o in ir if o.get("_sec") == sec and o.get("searchable")
              and o["type"] not in ("thinking", "redacted_thinking")]
    if not blocks:
        return False
    for o in blocks:
        text = "\n".join(o["content"]).strip()
        if text not in _BRIEF_HIDE_EXACT:
            return False
    return True


# ── lowering: brief ──

def _tid_result_ranges(ir):
    """Map short_tid → (start_line, end_line) for tool_result sections."""
    # Find which sec corresponds to which short_tid
    tid_sec = {}
    for o in ir:
        if o["type"] == "meta_header":
            c = o["content"]
            if c and len(c) >= 1:
                h = c[0]
                # [tool] name:AABBCC or [tool_error] name:AABBCC
                if h.startswith("[tool]") or h.startswith("[tool_error]"):
                    parts = h.split(":")
                    if len(parts) >= 2:
                        tid_sec[parts[-1]] = o.get("_sec")
    # Collect line ranges per sec
    sec_range = {}
    for o in ir:
        s = o.get("_sec")
        if s is None:
            continue
        sl = o.get("start_line")
        el = o.get("end_line")
        if sl is not None and el is not None:
            if s not in sec_range:
                sec_range[s] = [sl, el]
            else:
                sec_range[s][0] = min(sec_range[s][0], sl)
                sec_range[s][1] = max(sec_range[s][1], el)
    # Build tid → range (only content nodes, skip the separator/header)
    result = {}
    for tid, sec in tid_sec.items():
        if sec in sec_range:
            result[tid] = tuple(sec_range[sec])
    return result


def lower_brief(ir, truncate, filename="", truncate_user=256):
    short = _short(filename)
    roles = _sec_roles(ir)
    tid_ranges = _tid_result_ranges(ir)

    # Sections visible in truncation: not tool/tool_error/system
    visible_secs = {sec for sec, role in roles.items()
                     if role not in ("tool", "tool_error", "system")}

    # Determine which assistant sections to merge:
    # An assistant section merges if the previous VISIBLE section is also assistant.
    all_secs = sorted(roles.keys())
    merge_secs = set()
    for i in range(1, len(all_secs)):
        cur_sec = all_secs[i]
        if not (cur_sec in visible_secs and
                roles.get(cur_sec) == "assistant"):
            continue
        # Find previous visible section
        for j in range(i - 1, -1, -1):
            if all_secs[j] in visible_secs:
                if roles.get(all_secs[j]) == "assistant":
                    merge_secs.add(cur_sec)
                break

    # Track which secs only have thinking content (no visible non-thinking blocks)
    sec_has_nonthink = set()
    for o in ir:
        s = o.get("_sec")
        if s is None: continue
        if o["type"] not in ("meta", "meta_header", "thinking", "redacted_thinking"):
            sec_has_nonthink.add(s)

    # Sections that are all-thinking should still be hidden in truncation
    # (they have no visible content after thinking is removed)
    for sec in list(visible_secs):
        if sec not in sec_has_nonthink:
            visible_secs.discard(sec)
        elif roles.get(sec) == "user" and _user_hidden_in_brief(ir, sec):
            visible_secs.discard(sec)
        elif _section_hidden_exact(ir, sec):
            visible_secs.discard(sec)

    # Recompute merge_secs after possible visibility changes
    all_secs_2 = sorted(roles.keys())
    merge_secs = set()
    for i in range(1, len(all_secs_2)):
        cur_sec = all_secs_2[i]
        if not (cur_sec in visible_secs and
                roles.get(cur_sec) == "assistant"):
            continue
        for j in range(i - 1, -1, -1):
            if all_secs_2[j] in visible_secs:
                if roles.get(all_secs_2[j]) == "assistant":
                    merge_secs.add(cur_sec)
                break

    for idx, o in enumerate(ir):
        s = o.get("_sec")

        # Separator: replace with blank line in brief mode
        if s is None and o["type"] == "meta" and SEP in o.get("content", []):
            ns = None
            for j in range(idx + 1, len(ir)):
                ns = ir[j].get("_sec")
                if ns is not None:
                    break
            if ns is None or ns not in visible_secs:
                o["content_brief"] = None
            elif ns in merge_secs:
                o["content_brief"] = None
            elif not any(sec in visible_secs for sec in range(ns)):
                o["content_brief"] = None
            else:
                o["content_brief"] = [""]
            continue

        # Section not visible → hide
        if s is not None and s not in visible_secs:
            o["content_brief"] = None
            continue

        # Merged assistant: hide separator and header
        if s in merge_secs and o["type"] == "meta_header":
            o["content_brief"] = None
            continue

        # Thinking / redacted_thinking → hide (including their >>> <<< metas)
        if _is_thinking(o):
            o["content_brief"] = None
            continue
        if o["type"] == "meta":
            c = o["content"]
            if c and (c[0].startswith(">>>thinking") or c[0].startswith("<<<thinking") or
                      c[0].startswith(">>>redacted_thinking") or c[0].startswith("<<<redacted_thinking")):
                o["content_brief"] = None
                continue

        # Tool_call three-piece: collapse to single-line summary with line ref
        if o["type"] == "meta" and o.get("content", []):
            c0 = o["content"][0]
            if c0.startswith(">>>tool_call"):
                # Hide noise tools (internal bookkeeping)
                tool_name = c0.split()[1].split(":")[0] if len(c0.split()) > 1 else ""
                if tool_name in _BRIEF_HIDE_TOOLS:
                    o["content_brief"] = None
                    continue
                summary = o.get("_tool_summary", f"* unknown")
                s = o.get("start_line")
                e = o.get("end_line")
                for j in (idx + 1, idx + 2):
                    if j < len(ir) and ir[j].get("_blk") == o.get("_blk"):
                        je = ir[j].get("end_line")
                        if je is not None:
                            e = je
                if s is not None and e is not None:
                    # Extract short_tid and look up tool_result range
                    parts = c0.split()[1].split(":") if len(c0.split()) > 1 else []
                    stid = parts[-1] if len(parts) >= 2 else ""
                    rr = tid_ranges.get(stid)
                    if rr:
                        summary = f"{summary} ({short}:{s+1}-{e+1},{rr[0]+1}-{rr[1]+1})"
                    else:
                        summary = f"{summary} ({short}:{s+1}-{e+1})"
                o["content_brief"] = [summary]
                continue
            if c0 == "<<<tool_call":
                o["content_brief"] = None
                continue

        if o["type"] == "tool_call":
            o["content_brief"] = None
            continue

        # meta_header → copy
        if o["type"] == "meta_header":
            o["content_brief"] = list(o["content"])
            continue

        # meta → copy
        if o["type"] == "meta":
            o["content_brief"] = list(o["content"])
            continue

        # Truncatable content
        if _is_truncatable(o):
            node_start = o.get("start_line", 0) + 1
            node_end = o.get("end_line", o.get("start_line", 0)) + 1
            ref = f"{short}:{node_start}-{node_end}"
            text = "\n".join(o["content"])
            if o["type"] == "user":
                text = _strip_noise_xml(text)
                if not text.strip():
                    o["content_brief"] = None
                    continue
            lim = truncate_user if o["type"] == "user" else truncate
            lines = (_trunc(text, lim, ref) if lim else text).split("\n")
            # Strip leading blank lines
            while lines and not lines[0]:
                lines.pop(0)
            o["content_brief"] = lines
            continue

        # Non-truncatable (images, documents, etc) → copy
        o["content_brief"] = list(o["content"])


# ── lowering: view ──

def lower_view(ir, filename="", grep_pattern=None):
    if not grep_pattern:
        # No grep: view is same as truncated (shouldn't normally be called)
        for o in ir:
            o["content_view"] = o.get("content_brief")
        return

    short = _short(filename)

    # Per-node match check
    def _node_matches(o):
        if not o["searchable"]:
            return False
        for line in o["content"]:
            if grep_pattern.search(line):
                return True
        return False

    # Pass 1: determine visibility for each searchable block
    block_visible = {}  # blk -> bool
    for o in ir:
        blk = o.get("_blk")
        if blk is None or blk in block_visible:
            continue
        if o["searchable"] and _node_matches(o):
            block_visible[blk] = True

    # Derive which sections have any visible block (for header/separator logic)
    sec_has_visible = set()
    for o in ir:
        blk = o.get("_blk")
        if blk is not None and block_visible.get(blk):
            s = o.get("_sec")
            if s is not None:
                sec_has_visible.add(s)

    # Pass 2: set content_view for each node
    for idx, o in enumerate(ir):
        s = o.get("_sec")
        blk = o.get("_blk")

        # Separator: show only between two sections that have visible blocks
        if s is None and o["type"] == "meta" and SEP in o.get("content", []):
            next_vis = False
            for j in range(idx + 1, len(ir)):
                ns = ir[j].get("_sec")
                if ns is not None:
                    next_vis = ns in sec_has_visible
                    break
            prev_vis = False
            seen = set()
            for j in range(idx - 1, -1, -1):
                ps = ir[j].get("_sec")
                if ps is not None and ps not in seen:
                    seen.add(ps)
                    if ps in sec_has_visible:
                        prev_vis = True
                        break
            o["content_view"] = list(o["content"]) if (next_vis and prev_vis) else None
            continue

        # meta_header: show if section has any visible block
        if o["type"] == "meta_header":
            o["content_view"] = list(o["content"]) if s in sec_has_visible else None
            continue

        # Thinking / tool_call metas: show if same blk matched
        if o["type"] == "meta" and o.get("content", []):
            c0 = o["content"][0]
            if c0.startswith(">>>thinking") or c0.startswith("<<<thinking") or \
               c0.startswith(">>>redacted_thinking") or c0.startswith("<<<redacted_thinking") or \
               c0.startswith(">>>tool_call ") or c0 == "<<<tool_call":
                o["content_view"] = list(o["content"]) if block_visible.get(blk) else None
                continue

        # Other meta → show if section has visible blocks
        if o["type"] == "meta":
            o["content_view"] = list(o["content"]) if s in sec_has_visible else None
            continue

        # Searchable content blocks: show only if this block matches
        if o["searchable"]:
            if _node_matches(o):
                node_start = o.get("start_line", 0) + 1
                o["content_view"] = match_lines(
                    o["content"], grep_pattern, short, node_start)
            else:
                o["content_view"] = None
            continue

        # Non-searchable (images, docs, etc) → hide
        o["content_view"] = None


# ── codegen ──

def emit(ir, key="content"):
    lines = []
    for o, c, blank in _walk(ir, key):
        if blank: lines.append("")
        lines.extend(c)
    return lines


# ── grep ──

def _rel_path(fp):
    try:
        return os.path.relpath(fp)
    except ValueError:
        return os.path.abspath(fp)

def grep_search(results, pattern):
    first = True
    for filepath, ir in reversed(results):
        short = _rel_path(filepath)
        for o in reversed(ir):
            if not o["searchable"]: continue
            lines = match_lines(o["content"], pattern, short, o.get("start_line", 0) + 1)
            if len(lines) <= 1:
                continue
            if not first: print()
            first = False
            print(f"{lines[0]} [{o['type']}]")
            for lt in lines[1:]:
                print(lt)


# ── compile ──

def compile_pass(input_path, output_dir=None, truncate=128, truncate_user=256,
            grep_pattern=None, quiet=False):
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(input_path)) or "."
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(input_path))[0]

    raw_recs = lex(input_path)
    src_format = detect_format(raw_recs)
    if src_format == "codex":
        recs = raw_recs
        chains = split_codex_chains(recs)
    elif src_format == "omp":
        recs = raw_recs
        chains = split_omp_chains(recs)
    elif src_format == "t3code":
        recs = raw_recs
        chains = split_t3code_chains(recs)
    else:
        recs = merge_chunks(raw_recs)
        chains = split_chains(recs)
    if not chains:
        if not quiet:
            print("No conversation chains found.")
        return []

    results, paths = [], []

    for i, chain in enumerate(chains):
        sfx = f"_{i+1}" if len(chains) > 1 else ""
        ffn = f"{base}{sfx}.txt"
        mfn = f"{base}{sfx}.min.txt"
        vfn = f"{base}{sfx}.view.txt"
        fp = os.path.join(output_dir, ffn)
        mp = os.path.join(output_dir, mfn)
        vp = os.path.join(output_dir, vfn)
        data_ctr = [0]

        if src_format == "codex":
            ir = parse_codex(chain, output_dir, f"{base}{sfx}", data_ctr)
        elif src_format == "omp":
            ir = parse_omp(chain, output_dir, f"{base}{sfx}", data_ctr)
        elif src_format == "t3code":
            ir = parse_t3code(chain, output_dir, f"{base}{sfx}", data_ctr)
        else:
            ir = parse(chain, output_dir, f"{base}{sfx}", data_ctr)
        assign_lines(ir)
        lower_brief(ir, truncate, ffn, truncate_user)

        full = emit(ir, "content")
        brief = emit(ir, "content_brief")

        stats_footer = _collect_stats(chain) if src_format != "codex" else None
        if stats_footer:
            full.extend([""] + stats_footer)

        with open(fp, "w", encoding="utf-8") as f: f.write("\n".join(full))
        with open(mp, "w", encoding="utf-8") as f: f.write("\n".join(brief))

        if grep_pattern:
            lower_view(ir, ffn, grep_pattern)
            view = emit(ir, "content_view")
            with open(vp, "w", encoding="utf-8") as f: f.write("\n".join(view))

        ft, bt = "\n".join(full), "\n".join(brief)
        _cnt = lambda s: sum(1 for t in _tokenize(s) if t.strip())
        results.append((fp, ir))
        paths.append((fp, mp, vp if grep_pattern else None,
                       len(full), _cnt(ft), len(brief), _cnt(bt)))

    if not quiet:
        for fp, _, _, fl, fw, _, _ in paths:
            print(f"  {fp}  ({fl} lines, {fw} words)")
        for _, mp, _, _, _, bl, bw in paths:
            print(f"  {mp}  ({bl} lines, {bw} words)")
        if grep_pattern:
            for _, _, vp, _, _, _, _ in paths:
                if vp:
                    print(f"  {vp}")

    return results

# ── main ──

def _expand_inputs(raw):
    files = []
    for r in raw:
        expanded = globmod.glob(r, recursive=True)
        expanded.sort(key=lambda f: os.path.getmtime(f))
        files.extend(expanded if expanded else [r])
    return files

def main():
    p = argparse.ArgumentParser(description="VCC - View-oriented Conversation Compiler")
    p.add_argument("input", nargs="+")
    p.add_argument("-o", "--output-dir")
    p.add_argument("-t", "--truncate", nargs="?", type=int, const=128, default=128, metavar="N")
    p.add_argument("-tu", "--truncate-user", nargs="?", type=int, const=256, default=256, metavar="N")
    p.add_argument("--grep", metavar="PATTERN")
    a = p.parse_args()
    try:
        a.grep = re.compile(a.grep) if a.grep else None
    except re.error as e:
        p.error(f"invalid regex for --grep: {e}")
    all_results = []
    for f in _expand_inputs(a.input):
        res = compile_pass(f, a.output_dir, a.truncate, a.truncate_user,
                      a.grep, quiet=bool(a.grep))
        all_results.extend(res)
    if a.grep:
        grep_search(all_results, a.grep)

if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    try:
        main()
    except BrokenPipeError:
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        finally:
            sys.exit(0)
