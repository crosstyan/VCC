#!/usr/bin/env python3
"""Legacy single-module compiler pending the typed-IR split.

View-oriented Conversation Compiler for supported coding-agent logs.

Produces per conversation chain:
  .txt       Full transcript (lossless rendering of JSONL)
  .min.txt   Brief mode (tool_call summaries with line refs, no separators)

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
    """Bound search display lines by eliding embedded base64-like payloads."""
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

# ── search line formatting ──

def format_search_line(line_number, text):
    return f"  {line_number}: {_redact_search_blob_line(text)}"


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

def _sec_metadata(ir):
    """Map section IDs to structural header metadata."""
    sections = {}
    for o in ir:
        if o["type"] != "meta_header":
            continue
        sec = o.get("_sec")
        if sec is None:
            continue
        sections[sec] = {
            "role": o.get("_role"),
            "kind": o.get("_kind"),
            "brief_policy": o.get("_brief_policy", "hidden"),
        }
    return sections


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

def _tid_result_info(ir):
    """Map full tool IDs to structural result ranges and error outcomes."""
    tid_sections = {}
    for o in ir:
        if o["type"] != "meta_header" or o.get("_kind") != "tool_result":
            continue
        tid = o.get("_tid")
        sec = o.get("_sec")
        if tid is not None and sec is not None:
            tid_sections[tid] = (sec, bool(o.get("_tool_error")))

    sec_ranges = {}
    for o in ir:
        sec = o.get("_sec")
        if sec is None:
            continue
        start_line = o.get("start_line")
        end_line = o.get("end_line")
        if start_line is None or end_line is None:
            continue
        if sec not in sec_ranges:
            sec_ranges[sec] = [start_line, end_line]
        else:
            sec_ranges[sec][0] = min(sec_ranges[sec][0], start_line)
            sec_ranges[sec][1] = max(sec_ranges[sec][1], end_line)

    result = {}
    for tid, (sec, is_error) in tid_sections.items():
        if sec in sec_ranges:
            result[tid] = {
                "range": tuple(sec_ranges[sec]),
                "is_error": is_error,
            }
    return result


def lower_brief(ir, truncate, filename="", truncate_user=256):
    short = _short(filename)
    sections = _sec_metadata(ir)
    roles = {sec: metadata["role"] for sec, metadata in sections.items()}
    policies = {sec: metadata["brief_policy"] for sec, metadata in sections.items()}
    tid_results = _tid_result_info(ir)

    visible_secs = {
        sec for sec, policy in policies.items() if policy in {"full", "context"}
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
        elif policies.get(sec) == "context":
            continue
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

    context_content_emitted = set()
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

        # Structural context sections retain a one-line header and one content line.
        if s is not None and policies.get(s) == "context":
            if o["type"] == "meta_header":
                content = o.get("content", [])
                o["content_brief"] = [content[0]] if content else None
            elif s not in context_content_emitted and o["type"] != "meta":
                content = o.get("content", [])
                o["content_brief"] = [content[0]] if content else None
                context_content_emitted.add(s)
            else:
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

        # Tool_call three-piece: collapse to single-line summary with line ref.
        if o["type"] == "meta" and o.get("_tool_boundary") == "start":
            tool_name = o.get("_tool_name")
            if tool_name in _BRIEF_HIDE_TOOLS:
                o["content_brief"] = None
                continue
            summary = o.get("_tool_summary", "* unknown")
            start_line = o.get("start_line")
            end_line = o.get("end_line")
            for j in (idx + 1, idx + 2):
                if j < len(ir) and ir[j].get("_blk") == o.get("_blk"):
                    candidate_end = ir[j].get("end_line")
                    if candidate_end is not None:
                        end_line = candidate_end
            result = tid_results.get(o.get("_tid"))
            if start_line is not None and end_line is not None:
                if result:
                    result_start, result_end = result["range"]
                    summary = (
                        f"{summary} ({short}:{start_line+1}-{end_line+1},"
                        f"{result_start+1}-{result_end+1})"
                    )
                else:
                    summary = f"{summary} ({short}:{start_line+1}-{end_line+1})"
            if result and result["is_error"]:
                summary = f"{summary} !error"
            o["content_brief"] = [summary]
            continue
        if o["type"] == "meta" and o.get("_tool_boundary") == "end":
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
            quiet=False, conversation=None, harness=None):
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
        fp = os.path.join(output_dir, ffn)
        mp = os.path.join(output_dir, mfn)
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

        ft, bt = "\n".join(full), "\n".join(brief)
        _cnt = lambda s: sum(1 for t in _tokenize(s) if t.strip())
        results.append((fp, ir))
        paths.append((fp, mp, len(full), _cnt(ft), len(brief), _cnt(bt)))

    if not quiet:
        for fp, _, fl, fw, _, _ in paths:
            print(f"  {fp}  ({fl} lines, {fw} words)")
        for _, mp, _, _, bl, bw in paths:
            print(f"  {mp}  ({bl} lines, {bw} words)")

    return results
