---
name: conversation-compiler
description: "VCC for Codex and OMP. Compile and search session JSONL logs across harnesses into full, brief, and adaptive views."
---

# VCC for Codex and OMP

Compile Codex rollout JSONL logs from `~/.codex/sessions/**/*.jsonl` and OMP session logs from `~/.omp/agent/sessions/**/*.jsonl` into agent-friendly views.

## Usage

```bash
uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc <command> [options]
```

## Output Files

| File | When | Description |
|------|------|-------------|
| `.txt` | Always | Full transcript |
| `.min.txt` | Always | Brief UI-oriented view |
| `.view.txt` | `xcc search` | Search-focused adaptive view |
| stdout | `xcc search` | Matching blocks with line-range references |

## Rules

- Use Codex rollout files from `~/.codex/sessions`, not `~/.codex/history.jsonl`. `history.jsonl` is prompt history only and is not a full transcript.
- Use OMP session files from `${PI_CODING_AGENT_DIR:-~/.omp/agent}/sessions`, not rendered exports.
- Prefer `xcc search` for cross-harness history search. Do not replace it with system `grep` or `rg`.
- Do not use `-o` unless the user explicitly asks for a separate output directory.
- Leave compiled output files in place unless the user explicitly asks to remove them.
- Codex reasoning is unsupported in this fork. If the rollout stores encrypted reasoning, VCC hides it completely.

## Workflow

**1. Search across harnesses**

Search recent current-project sessions from both harnesses:

```bash
uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc search "keyword" --harness all --cwd "$PWD"
```

If no exact current-project match exists, broaden the search by omitting `--cwd`.

**2. Read search refs**

`xcc search` emits full-view refs such as `/path/session.txt:24-31`. Read them with:

```bash
uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc read "/path/session.txt:24-31"
```

**3. List or compile sessions**

List recent sessions:

```bash
uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc list --harness all --cwd "$PWD" --limit 20
```

Compile explicit Codex or OMP JSONL files:

```bash
uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc compile "/absolute/path/to/session.jsonl"
```

Codex and OMP compaction boundaries are split into numbered chunks automatically. Higher chunk numbers are newer.

**4. Read `.min.txt`**

The `.min.txt` file is the default scan surface. It keeps user and assistant text, collapses tool calls to one-line summaries, and includes pointers back into `.txt`.

Read `.view.txt` only when you want the focused projection. For precise context recovery, jump into the referenced `.txt` ranges.

**5. Jump to `.txt`**

All references resolve against `.txt`, which is the canonical line-number coordinate system for that chunk.

# Skills

## `readchat` - Read a specific Codex or OMP session

Trigger when the user wants to inspect a specific session JSONL file, thread file, or a path under `~/.codex/sessions` / `${PI_CODING_AGENT_DIR:-~/.omp/agent}/sessions`.

Action:

1. Compile the session with `uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc compile "<session.jsonl>"`.
2. Read the newest `.min.txt` chunk first.
3. Use `xcc search` and `.txt` line jumps as needed.

## `searchchat` - Search across Codex and OMP sessions

Trigger when the user wants to search prior Codex or OMP sessions.

Action:

1. Run `uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc search "keyword" --harness all --cwd "$PWD"`.
2. If no useful results appear, broaden by omitting `--cwd`.
3. Read relevant refs with `uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc read "<ref>"`.

## `recall` - Recover context from recent Codex or OMP sessions

Trigger when the user asks to recall prior context, continue previous work, or recover what was discussed earlier.

Action:

1. Use `uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc list --harness all --cwd "$PWD" --latest` to locate the newest current-project session.
2. If nothing matches, broaden by omitting `--cwd` and choose the most relevant recent session.
3. Compile it with `uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc compile "<session.jsonl>"`.
4. Read the newest `.min.txt` chunk first, then use `xcc search` and `xcc read` to recover details.
5. After recovering context, verify any referenced files or repository state directly from the workspace before relying on the transcript.
