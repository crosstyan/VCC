---
name: conversation-compiler
description: "VCC for Codex. Compile Codex rollout JSONL logs into full, brief, and adaptive views."
---

# VCC for Codex

Compile Codex rollout JSONL logs from `~/.codex/sessions/**/*.jsonl` into agent-friendly views.

## Usage

```bash
python "~/.codex/skills/conversation-compiler/scripts/VCC.py" <rollout.jsonl ...> [options]
```

```bash
python "~/.codex/skills/conversation-compiler/scripts/find_codex_rollouts.py" [options]
```

## Output Files

| File | When | Description |
|------|------|-------------|
| `.txt` | Always | Full transcript |
| `.min.txt` | Always | Brief UI-oriented view |
| `.view.txt` | `--grep` only | Search-focused adaptive view |
| stdout | `--grep` only | Matching blocks with line-range references |

## Rules

- Use Codex rollout files from `~/.codex/sessions`, not `~/.codex/history.jsonl`. `history.jsonl` is prompt history only and is not a full transcript.
- Always use VCC `--grep` for content search on rollout files. Do not replace it with system `grep` or `rg`.
- Do not use `-o` unless the user explicitly asks for a separate output directory.
- Leave compiled output files in place unless the user explicitly asks to remove them.
- Codex reasoning is unsupported in this fork. If the rollout stores encrypted reasoning, VCC hides it completely.

## Workflow

**1. Find rollout files**

Use the helper to find relevant sessions:

```bash
python "~/.codex/skills/conversation-compiler/scripts/find_codex_rollouts.py" --cwd "$PWD" --latest
```

List recent sessions for the current project:

```bash
python "~/.codex/skills/conversation-compiler/scripts/find_codex_rollouts.py" --cwd "$PWD" --limit 20
```

If no exact current-project match exists, broaden the search by omitting `--cwd`.

**2. Compile**

```bash
python "~/.codex/skills/conversation-compiler/scripts/VCC.py" "/absolute/path/to/rollout.jsonl"
```

Codex compaction boundaries are split into numbered chunks automatically. Higher chunk numbers are newer.

**3. Read `.min.txt`**

The `.min.txt` file is the default scan surface. It keeps user and assistant text, collapses tool calls to one-line summaries, and includes pointers back into `.txt`.

**4. Search with `--grep`**

```bash
python "~/.codex/skills/conversation-compiler/scripts/VCC.py" "/absolute/path/to/rollout.jsonl" --grep "keyword"
```

For many rollouts, pass multiple files in one invocation:

```bash
python "~/.codex/skills/conversation-compiler/scripts/VCC.py" /path/a.jsonl /path/b.jsonl --grep "keyword"
```

Read `.view.txt` only when you want the focused projection. For precise context recovery, jump into the referenced `.txt` ranges.

**5. Jump to `.txt`**

All references resolve against `.txt`, which is the canonical line-number coordinate system for that chunk.

# Skills

## `readchat` - Read a specific Codex rollout

Trigger when the user wants to inspect a specific rollout file, thread file, or `~/.codex/sessions/...jsonl`.

Action:

1. Compile the rollout with `VCC.py`.
2. Read the newest `.min.txt` chunk first.
3. Use `--grep` and `.txt` line jumps as needed.

## `searchchat` - Search across Codex rollouts

Trigger when the user wants to search prior Codex sessions.

Action:

1. Use `find_codex_rollouts.py --cwd "$PWD" --limit 20` first.
2. If no useful results appear, broaden by omitting `--cwd`.
3. Run one VCC `--grep` command across the selected rollout files.
4. Read the referenced `.txt` ranges for the relevant hits.

## `recall` - Recover context from recent Codex sessions

Trigger when the user asks to recall prior context, continue previous work, or recover what was discussed earlier.

Action:

1. Use `find_codex_rollouts.py --cwd "$PWD" --latest` to locate the newest current-project rollout.
2. If nothing matches, broaden the search and choose the most relevant recent rollout.
3. Compile the rollout with VCC.
4. Read the newest `.min.txt` chunk first, then use `--grep` and `.txt` jumps to recover details.
5. After recovering context, verify any referenced files or repository state directly from the workspace before relying on the transcript.
