---
name: verify
description: Drive the xcc CLI against isolated temporary history stores.
---

# Verify VCC history changes

Exercise the installed CLI, not internal Python functions or the unit suite.

## Setup

1. Install the current checkout: `uv tool install --editable . --force`.
2. Create a temporary root and isolate every history source with environment variables:
   - `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR`, `T3CODE_HOME`, `REASONIX_HOME`
   - `VCC_CACHE_DIR`
3. Write minimal JSONL/SQLite fixtures under that temporary root. For Claude, include housekeeping before a real `user`/`assistant` message and place optional agents under `projects/<project>/<session>/subagents/agent-*.jsonl`.

## Drive the CLI

- `xcc --help`
- `xcc list --harness <source> --json`
- `xcc list --harness claude --cwd <symlink-alias> --json`
- Compare Claude list/search with and without `--include-subagents`.
- `xcc search <pattern> --harness <source> --json`, then pass the returned `ref` to `xcc read`.
- Confirm search outputs resolve under the canonical `VCC_CACHE_DIR` and repeated searches reuse the same compiled path.
- Confirm search does not add `.txt` files beside source logs.
- Run `xcc compile <explicit-source>` and confirm explicit compilation still writes beside that source.
- For live JSONL behavior, include a malformed newline-terminated record and an invalid unterminated final fragment; confirm valid surrounding content remains searchable and the completed malformed line produces a warning.

## Probes

- Search for subagent-only content without `--include-subagents`; expect no hits.
- Pass an invalid regex; expect a clear Click error and exit status 2.
- Confirm both compiled search directories and exported T3 projection directories are mode `0700`.
- Replace `compiled/` or `t3code/` with a symlink to an outside directory; expect a refusal and no outside writes.

Capture CLI stdout/stderr and report a PASS/FAIL/BLOCKED verdict with the exact observed outputs.
