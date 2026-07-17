#!/usr/bin/env python3
"""Legacy single-module compiler pending the typed-IR split.

View-oriented Conversation Compiler for supported coding-agent logs.

Produces per conversation chain:
  .txt       Full transcript (lossless rendering of JSONL)
  .min.txt   Brief mode (tool_call summaries with line refs, no separators)
  .view.txt  View mode (search-focused, written by xcc search)

Usage:
  xcc compile conversation.jsonl
  xcc search "kw" --harness all
"""

import json
import os
import re
import sys

from .events import Conversation
from .frontends.claude import parse_events as parse_claude_events
from .frontends.claude import split_record_segments as split_claude_record_segments
from .frontends.codex import parse_events as parse_codex_events
from .frontends.codex import split_record_segments as split_codex_record_segments
from .frontends.omp import parse_events as parse_omp_events
from .frontends.omp import split_record_segments as split_omp_record_segments
from .frontends.reasonix import parse_events as parse_reasonix_events
from .frontends.t3code import parse_events as parse_t3code_events
from .registry import HARNESS_IDS
from .render import SEP, render as render_conversation

# ── tokenizer ──

_TOK_RE = re.compile(
    r'[a-zA-Z]+'           # letters (grouped)
    r'|[0-9]+'             # digits (grouped)
    r'|[^\sa-zA-Z0-9]'     # single char: any non-whitespace non-letter non-digit
    r'|\s+'                # whitespace (preserved, not counted)
)
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

def _short(fn):
    n, e = os.path.splitext(fn)
    return ("#" + n[-6:] + e) if len(n) > 12 else fn


def lex(path):
    records = []
    with open(path, "rb") as f:
        remaining = os.fstat(f.fileno()).st_size
        line_number = 0
        while remaining > 0:
            raw_line = f.readline(remaining)
            if not raw_line:
                break
            remaining -= len(raw_line)
            line_number += 1
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                if not raw_line.endswith((b"\n", b"\r")) and remaining == 0:
                    continue
                print(
                    f"warning: {path}:{line_number}: malformed JSON record: {error}",
                    file=sys.stderr,
                )
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def detect_format(recs):
    for r in recs:
        rt = r.get("type")
        if rt in {"t3_session", "t3_message", "t3_custom_message", "t3_activity"}:
            return "t3code"
        if rt == "session":
            return "omp"
        if rt in {"session_meta", "response_item", "event_msg", "turn_context", "compacted"}:
            return "codex"
        if r.get("role") in {"system", "user", "assistant", "tool"}:
            return "reasonix"
        if rt in {"assistant", "user", "system"}:
            return "claude"
    return None


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
    """Map sec -> structural role, falling back to legacy header parsing."""
    roles = {}
    for o in ir:
        if o["type"] == "meta_header":
            s = o.get("_sec")
            if s is None:
                continue
            role = o.get("_role")
            if role:
                roles[s] = role
                continue
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
    """Map full tool ID → (start_line, end_line) for tool_result sections."""
    # Find which sec corresponds to which tool ID
    tid_sec = {}
    for o in ir:
        if o["type"] == "meta_header":
            c = o["content"]
            if c and len(c) >= 1:
                h = c[0]
                if h.startswith("[tool]") or h.startswith("[tool_error]"):
                    if "_tid" in o:
                        tid_sec[o["_tid"]] = o.get("_sec")
                    else:
                        # Defensive fallback for IR built without structural IDs.
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

    # Sections visible in truncation: conversational user/assistant content only.
    visible_secs = {
        sec
        for sec, role in roles.items()
        if role not in ("tool", "tool_error", "system", "custom")
        and not role.startswith("custom:")
    }

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
                tool_name = o.get("_tool_name")
                if not isinstance(tool_name, str):
                    # Defensive fallback for IR built without structural tool names.
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
                    if "_tid" in o:
                        tid = o["_tid"]
                    else:
                        # Defensive fallback for IR built without structural IDs.
                        parts = c0.split()[1].split(":") if len(c0.split()) > 1 else []
                        tid = parts[-1] if len(parts) >= 2 else ""
                    rr = tid_ranges.get(tid)
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


# ── compile ──

_FRONTEND_PARSERS = {
    "claude": parse_claude_events,
    "codex": parse_codex_events,
    "omp": parse_omp_events,
    "reasonix": parse_reasonix_events,
    "t3code": parse_t3code_events,
}
_RECORD_SEGMENT_SPLITTERS = {
    "claude": split_claude_record_segments,
    "codex": split_codex_record_segments,
    "omp": split_omp_record_segments,
}


def _harness_error(prefix):
    choices = ", ".join(HARNESS_IDS)
    return ValueError(f"{prefix}; pass --harness with one of: {choices}")


def _record_chains(harness, records, segment_count):
    splitter = _RECORD_SEGMENT_SPLITTERS.get(harness)
    chains = list(splitter(records)) if splitter else [records] * segment_count
    if len(chains) < segment_count:
        chains.extend([records] * (segment_count - len(chains)))
    return chains[:segment_count]


def compile_pass(input_path, output_dir=None, truncate=128, truncate_user=256,
            grep_pattern=None, quiet=False, conversation=None, harness=None):
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(input_path)) or "."
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(input_path))[0]

    if conversation is not None and not isinstance(conversation, Conversation):
        raise TypeError("conversation must be a Conversation")
    if conversation is not None and harness is not None and harness != conversation.harness:
        raise ValueError(
            f"conversation harness {conversation.harness!r} does not match {harness!r}"
        )

    raw_recs = [] if conversation is not None else lex(input_path)
    src_format = conversation.harness if conversation is not None else harness
    if src_format is None:
        src_format = detect_format(raw_recs)
        if src_format is None:
            raise _harness_error(f"could not detect session harness for {input_path}")
    parser = _FRONTEND_PARSERS.get(src_format)
    if parser is None:
        raise _harness_error(f"unsupported harness {src_format!r}")

    parsed_conversation = conversation or parser(raw_recs)
    chains = _record_chains(
        src_format,
        raw_recs,
        len(parsed_conversation.segments),
    )
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

        ir = render_conversation(
            parsed_conversation,
            output_dir,
            f"{base}{sfx}",
            data_ctr,
            segment_index=i,
        )
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
