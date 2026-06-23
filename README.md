（this readme is written by human）

# VCC: View-oriented Conversation Compiler

Unofficial implementation of "View-oriented Conversation Compiler for Agent Trace Analysis" ([Paper](https://arxiv.org/abs/2603.29678)).

This fork targets user-level skills and session logs for Codex, OMP, and Reasonix, without modifying any harness source tree.

## What This Fork Does

VCC compiles Codex rollout JSONL files, OMP session JSONL files, and Reasonix session JSONL files into:

- `.txt`: full transcript
- `.min.txt`: brief UI-oriented view
- `.view.txt`: adaptive search projection when `xcc search` is used

The key invariant from the paper stays the same: line numbers are assigned once in the full view, and every other view points back into that same coordinate system.

## History Sources

- Use rollout files from `~/.codex/sessions/**/*.jsonl`.
- Do not use `~/.codex/history.jsonl` for VCC. It is only prompt history, not a full transcript.
- Use OMP session files from `${PI_CODING_AGENT_DIR:-~/.omp/agent}/sessions/**/*.jsonl`.
- Use Reasonix session files from `${REASONIX_HOME:-~/.reasonix}/sessions/**/*.jsonl` and `${REASONIX_HOME:-~/.reasonix}/projects/*/sessions/**/*.jsonl`.
- Codex reasoning is hidden in this fork. Real rollouts persist reasoning as encrypted content, so VCC does not attempt to render it.
- Codex compaction and OMP compaction are split into numbered chunks automatically. Newer chunks get larger suffixes.

## Install

See [INSTALL.md](INSTALL.md).

The supported skill install surface for this fork is:

`~/.codex/skills/{conversation-compiler,readchat,recall,searchchat}`

for OMP:

`${PI_CODING_AGENT_DIR:-~/.omp/agent}/skills/{conversation-compiler,readchat,recall,searchchat}`

and, for Reasonix:

`${REASONIX_HOME:-~/.reasonix}/skills/{conversation-compiler,readchat,recall,searchchat}`

## Basic Usage

The cross-harness CLI is `xcc`:

```bash
uv --project /home/crosstyan/Code/VCC run xcc list --harness all --cwd "$PWD" --limit 20
```

Search recent sessions from all supported harnesses:

```bash
uv --project /home/crosstyan/Code/VCC run xcc search "keyword" --harness all --cwd "$PWD"
```

Read a search result ref:

```bash
uv --project /home/crosstyan/Code/VCC run xcc read "/absolute/path/to/session.txt:24-31"
```

Compile one explicit Codex, OMP, or Reasonix session file:

```bash
uv --project /home/crosstyan/Code/VCC run xcc compile "/absolute/path/to/session.jsonl"
```

## Skills

This fork ships four skills:

- `conversation-compiler`
- `readchat`
- `recall`
- `searchchat`

After installation, ask Codex, OMP, or Reasonix to use those skills directly. The wrapper skills route into `conversation-compiler`.

## How It Works

Codex rollouts contain structured items such as:

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

OMP sessions use `type: "session"` headers plus append-only entries such as `message`, `model_change`, `compaction`, `branch_summary`, and `custom_message`.

Reasonix sessions store provider messages directly as JSONL records with `role`, `content`, `reasoning_content`, `tool_calls`, and tool-result fields. The `xcc` CLI discovers all supported harnesses, compiles each session through the same VCC views, and emits refs that `xcc read` can resolve.

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

- `pyproject.toml`: PEP 621 package metadata for the `vcc-history` uv project
- `src/vcc_history/xcc.py`: typed Click cross-harness CLI
- `skills/conversation-compiler/scripts/VCC.py`: compiler implementation used by the CLI
- `skills/*/SKILL.md`: skill definitions

## Paper

- arXiv abstract: https://arxiv.org/abs/2603.29678
- HTML: https://arxiv.org/html/2603.29678v2
