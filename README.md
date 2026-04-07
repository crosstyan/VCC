（this readme is written by human）

# VCC: View-oriented Conversation Compiler

Unofficial implementation of "View-oriented Conversation Compiler for Agent Trace Analysis" ([Paper](https://arxiv.org/abs/2603.29678)).

This fork targets Codex user-level skills and Codex rollout logs under `~/.codex/sessions`.

## What This Fork Does

VCC compiles Codex rollout JSONL files into:

- `.txt`: full transcript
- `.min.txt`: brief UI-oriented view
- `.view.txt`: adaptive search projection when `--grep` is used

The key invariant from the paper stays the same: line numbers are assigned once in the full view, and every other view points back into that same coordinate system.

## Important Codex Notes

- Use rollout files from `~/.codex/sessions/**/*.jsonl`.
- Do not use `~/.codex/history.jsonl` for VCC. It is only prompt history, not a full transcript.
- Codex reasoning is hidden in this fork. Real rollouts persist reasoning as encrypted content, so VCC does not attempt to render it.
- Codex compaction is split into numbered chunks automatically. Newer chunks get larger suffixes.

## Install

See [INSTALL.md](INSTALL.md).

The supported install surface for this fork is:

`~/.codex/skills/{conversation-compiler,readchat,recall,searchchat}`

## Basic Usage

Find the latest rollout for the current project:

```bash
python "~/.codex/skills/conversation-compiler/scripts/find_codex_rollouts.py" --cwd "$PWD" --latest
```

Compile one rollout:

```bash
python "~/.codex/skills/conversation-compiler/scripts/VCC.py" "/absolute/path/to/rollout.jsonl"
```

Search one or more rollouts:

```bash
python "~/.codex/skills/conversation-compiler/scripts/VCC.py" "/absolute/path/to/rollout.jsonl" --grep "keyword"
```

Search recent rollouts for the current project:

```bash
python "~/.codex/skills/conversation-compiler/scripts/VCC.py" \
  $(python "~/.codex/skills/conversation-compiler/scripts/find_codex_rollouts.py" --cwd "$PWD" --limit 20) \
  --grep "keyword"
```

## Codex Skills

This fork ships four Codex skills:

- `conversation-compiler`
- `readchat`
- `recall`
- `searchchat`

After installation, ask Codex to use those skills directly. The wrapper skills route into `conversation-compiler`.

## How It Works

A Codex rollout contains structured items such as:

- `session_meta`
- `response_item.message`
- `response_item.function_call`
- `response_item.function_call_output`
- `response_item.web_search_call`
- `compacted`

VCC lowers those records into:

- a full transcript
- a UI-style brief transcript with collapsed tool-call summaries
- an adaptive block-preserving search view

Unlike flat `grep`, the adaptive view preserves role and block boundaries and gives exact pointers back into the full view.

## Example

Brief view:

```text
[user]

please inspect the repo

[assistant]

I’ll inspect the repo structure first.

* exec_command "pwd && ls -la" (example.txt:18-20,23-40)
```

Full view:

```text
[user]

please inspect the repo

══════════════════════════════
[assistant]

I’ll inspect the repo structure first.

══════════════════════════════
[assistant]

>>>tool_call exec_command:abc123
cmd: pwd && ls -la
workdir: /repo
<<<tool_call

══════════════════════════════
[tool] exec_command:abc123

Command: /usr/bin/zsh -lc 'pwd && ls -la'
...
```

## Repository Layout

- `skills/conversation-compiler/scripts/VCC.py`: compiler
- `skills/conversation-compiler/scripts/find_codex_rollouts.py`: Codex rollout discovery helper
- `skills/*/SKILL.md`: Codex skill definitions

## Paper

- arXiv abstract: https://arxiv.org/abs/2603.29678
- HTML: https://arxiv.org/html/2603.29678v2
