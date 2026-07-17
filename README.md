（this readme is written by human）

# VCC: View-oriented Conversation Compiler

Unofficial implementation of "View-oriented Conversation Compiler for Agent Trace Analysis" ([Paper](https://arxiv.org/abs/2603.29678)).

This fork targets user-level skills and session logs for Claude Code, Codex, OMP, T3 Code, and Reasonix, without modifying any harness source tree.

## What This Fork Does

VCC compiles Claude Code transcripts, Codex rollout JSONL files, OMP session JSONL files, T3 Code persisted threads, and Reasonix session JSONL files into:

- `.txt`: full transcript and canonical line-number coordinate system
- `.min.txt`: brief UI-oriented view with refs into `.txt`

`xcc search` matches structural transcript nodes directly and returns refs into the same `.txt` coordinate system. The key invariant from the paper stays the same: line numbers are assigned once in the full view, and every brief or search ref points back into that coordinate system.

## History Sources

- Claude Code: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/*/*.jsonl`. `xcc list` and `xcc search` use main-session transcripts by default; pass `--include-delegated` to include nested `subagents/agent-*.jsonl` transcripts.
- Codex: `${CODEX_HOME:-$HOME/.codex}/sessions/**/*.jsonl`. Do not use `history.jsonl`; it is prompt history, not a full transcript.
- OMP: `${PI_CODING_AGENT_DIR:-$HOME/.omp/agent}/sessions/**/*.jsonl`.
- T3 Code: `${T3CODE_HOME:-$HOME/.t3}/{userdata,dev}/state.sqlite`.
- Reasonix: `${REASONIX_HOME:-$HOME/.reasonix}/sessions/**/*.jsonl` and `${REASONIX_HOME:-$HOME/.reasonix}/projects/*/sessions/**/*.jsonl`.
- Missing harness stores are skipped when `--harness all` is used.
- T3 Code keeps its thread index in SQLite. For Codex-backed T3 threads, VCC uses T3's provider logs to find and compile the underlying Codex rollout when it exists; otherwise it exports the SQLite projection into a `t3code` JSONL cache under `${VCC_CACHE_DIR:-$HOME/.cache/vcc-history}`.
- Codex reasoning is hidden in this fork. Real rollouts persist reasoning as encrypted content, so VCC does not attempt to render it.
- Claude Code, Codex, and OMP compaction boundaries are split into numbered chunks automatically. Higher suffixes are newer.

## Install

See [INSTALL.md](INSTALL.md).

Install the CLI from the repository root with:

```bash
uv tool install --editable .
```

The supported user-level skill install surfaces are:

- Claude Code: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/{conversation-compiler,readchat,recall,searchchat}`
- Codex: `${CODEX_HOME:-$HOME/.codex}/skills/{conversation-compiler,readchat,recall,searchchat}`
- OMP: `${PI_CODING_AGENT_DIR:-$HOME/.omp/agent}/skills/{conversation-compiler,readchat,recall,searchchat}`
- Reasonix: `${REASONIX_HOME:-$HOME/.reasonix}/skills/{conversation-compiler,readchat,recall,searchchat}`

T3 Code history is available through `xcc`; it does not need these skills installed.

## Basic Usage

The cross-harness CLI is the installed `xcc` command.

List recent main sessions for the current project:

```bash
xcc list --harness all --cwd "$PWD" --limit 20
```

Add `--json` for structured records. Each record includes normalized UTC `created_at` and `updated_at` values, the compatibility `timestamp` alias (updated time, falling back to created time), and `kind` (`main` or `delegated`).

Search recent main sessions from all supported harnesses:

```bash
xcc search "keyword" --harness all --cwd "$PWD"
```

Delegated (subagent) transcripts are intentionally excluded on the first pass for every harness. If the main-session search misses context that may have been delegated, retry with:

```bash
xcc search "keyword" --harness all --cwd "$PWD" --include-delegated
```

Read a search result ref:

```bash
xcc read "/absolute/path/to/session.txt:24-31"
```

Compile one explicit Claude Code, Codex, OMP, T3 Code, or Reasonix session path or T3 thread ref:

```bash
xcc compile "/absolute/path/to/session.jsonl"
```

`xcc search` automatically writes disposable compiled views beneath `${VCC_CACHE_DIR:-$HOME/.cache/vcc-history}/compiled/` and returns refs into that cache. Unchanged sessions reuse a fingerprinted structural node index, while cache updates and T3 Code thread exports are locked and atomically published so concurrent searches cannot interleave writes. In contrast, `xcc compile` is an explicit export: it writes `.txt` and `.min.txt` beside the input by default, or under the directory supplied with `-o`.

Manage disposable cache data with:

```bash
xcc cache prune                 # remove orphaned or stale entries
xcc cache prune --max-age 30    # also remove entries older than 30 days
xcc cache clear                 # remove all compiled and T3 Code cache data
```

## Skills

This fork ships four self-contained skills:

- `conversation-compiler`
- `readchat`
- `recall`
- `searchchat`

After installation, ask Claude Code, Codex, OMP, or Reasonix to use the relevant skill directly.

## How It Works

Claude Code transcripts contain typed user, assistant, tool, progress, summary, and compaction records. Codex rollouts contain structured items such as:

- `session_meta`
- `response_item.message`
- `response_item.function_call`
- `response_item.function_call_output`
- `response_item.web_search_call`
- `compacted`

VCC lowers those records into:

- a full transcript
- a UI-style brief transcript with collapsed tool-call summaries

Search runs against the same structural nodes used to render those files. Unlike flat `grep`, it preserves block boundaries and returns exact pointers into the full view without writing a separate search projection.

OMP sessions use `type: "session"` headers plus append-only entries such as `message`, `model_change`, `compaction`, `branch_summary`, and `custom_message`. T3 Code stores its UI projection in SQLite tables such as `projection_threads`, `projection_thread_messages`, and `projection_thread_activities`, with per-thread provider logs under `${T3CODE_HOME:-$HOME/.t3}/{userdata,dev}/logs/provider`. Reasonix stores provider messages directly as JSONL records with `role`, `content`, `reasoning_content`, `tool_calls`, and tool-result fields. The `xcc` CLI discovers available sources, compiles each session through the same VCC views, and emits refs that `xcc read` can resolve.

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

- `pyproject.toml`: PEP 621 package metadata and the installed `xcc` entry point
- `src/vcc_history/cli.py`: typed Click frontend for the `xcc` command
- `src/vcc_history/history/service.py`: cross-harness history orchestration
- `src/vcc_history/history/registry.py`: ordered harness descriptors and source factories
- `src/vcc_history/history/sources/`: Claude Code, Codex, OMP, T3 Code, and Reasonix history adapters
- `src/vcc_history/history/compiler.py`: typed wrapper around the packaged VCC compiler
- `src/vcc_history/history/vcc.py`: legacy single-module compiler pending the typed-IR split
- `skills/*/SKILL.md`: skill definitions

## Paper

- arXiv abstract: https://arxiv.org/abs/2603.29678
- HTML: https://arxiv.org/html/2603.29678v2
