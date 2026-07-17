---
name: conversation-compiler
description: "VCC for Claude Code, Codex, OMP, T3 Code, and Reasonix. Compile and search session history across harnesses into full, brief, and adaptive views."
---

# VCC for Claude Code, Codex, OMP, T3 Code, and Reasonix

Use the installed `xcc` command to search or compile conversation history.

## Sources

- Claude Code: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/*/*.jsonl`; nested `subagents/agent-*.jsonl` files are opt-in with `--include-delegated`.
- Codex: `${CODEX_HOME:-$HOME/.codex}/sessions/**/*.jsonl`, not prompt-only `history.jsonl`.
- OMP: `${PI_CODING_AGENT_DIR:-$HOME/.omp/agent}/sessions/**/*.jsonl`.
- T3 Code: `${T3CODE_HOME:-$HOME/.t3}/{userdata,dev}/state.sqlite`; use `xcc list --harness t3code` to select a `t3code:...#thread` ref. Codex-backed threads resolve to their rollout when available, otherwise VCC exports the SQLite projection to its cache.
- Reasonix: `${REASONIX_HOME:-$HOME/.reasonix}/sessions/**/*.jsonl` and `${REASONIX_HOME:-$HOME/.reasonix}/projects/*/sessions/**/*.jsonl`.

Missing stores are skipped for `--harness all`.

## Views and Output

| File | Created by | Purpose |
|------|------------|---------|
| `.txt` | `xcc search` or `xcc compile` | Canonical full transcript and line-number coordinate system |
| `.min.txt` | `xcc search` or `xcc compile` | Brief scan view with tool summaries and refs into `.txt` |
| `.view.txt` | `xcc search` | Search-focused adaptive projection |

`xcc search` automatically stores disposable compiled views under `${VCC_CACHE_DIR:-$HOME/.cache/vcc-history}/compiled/` and prints matching blocks with full-view refs. `xcc compile` is an explicit export: it writes `.txt` and `.min.txt` beside the input unless `-o <directory>` is supplied.

Leave explicit compile output in place unless the user asks to remove it. Do not use `-o` unless a separate output directory is useful or requested.

## Search Workflow

1. Search current-project main sessions first:

   ```bash
   xcc search "pattern" --harness all --cwd "$PWD"
   ```

   `pattern` is a regular expression. Add `--ignore-case` when appropriate.

2. If useful Claude Code context may be in a delegated task, retry with subagents:

   ```bash
   xcc search "pattern" --harness all --cwd "$PWD" --include-delegated
   ```

3. If the project filter is too narrow, repeat without `--cwd`. Keep the main-session-first order; add `--include-delegated` only as a retry.

4. Read a returned ref such as `/path/session.txt:24-31`:

   ```bash
   xcc read "/path/session.txt:24-31"
   ```

Prefer `xcc search` over raw `grep` or `rg` for history search because it preserves transcript blocks and canonical refs.

## List and Compile Workflow

List recent current-project main sessions:

```bash
xcc list --harness all --cwd "$PWD" --limit 20
```

Retry with `--include-delegated` only when nested Claude Code work is relevant.

Compile a session path or T3 thread ref exactly as printed by `xcc list`:

```bash
xcc compile "<session-path-or-t3-ref>"
```

Do not compile a bare T3 `state.sqlite` as a single conversation. Select a thread ref with `xcc list --harness t3code`; use `xcc compile state.sqlite --all-threads -o <directory>` only for an intentional bulk export.

Read the highest-numbered `.min.txt` chunk first, then follow its refs with `xcc read`. Claude Code, Codex, and OMP compaction boundaries create numbered chunks; higher suffixes are newer.

## Rules

- Search main sessions before including Claude Code subagents.
- Codex encrypted reasoning is unsupported and remains hidden.
- Treat `.txt` as the canonical line-number space for every view.
- For recovered historical claims about files or repository state, verify the current workspace before acting on them.
