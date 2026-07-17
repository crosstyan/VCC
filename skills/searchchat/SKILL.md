---
name: searchchat
description: "Search across Claude Code, Codex, OMP, T3 Code, and Reasonix session history. Use when the user wants to search prior chat history from available harnesses."
---

# Search Conversation History

Use the installed `xcc` command. Search patterns are regular expressions.

1. Search current-project main sessions first:

   ```bash
   xcc search "pattern" --harness all --cwd "$PWD"
   ```

   Add `--ignore-case` when appropriate.

2. If relevant work may have been delegated, retry the same search with:

   ```bash
   xcc search "pattern" --harness all --cwd "$PWD" --include-delegated
   ```

3. If the project filter is too narrow, repeat without `--cwd`. Preserve the main-session-first order and include delegated sessions only as a retry.
4. If the default recent-session scan still finds nothing, search exhaustively:

   ```bash
   xcc search "pattern" --harness all --session-limit 0
   ```

   Add `--include-delegated` as well if delegated work is relevant.
5. Read useful refs returned by the search:

   ```bash
   xcc read "/path/to/session.txt:START-END"
   ```

`xcc search` automatically compiles disposable `.txt`, `.min.txt`, and `.view.txt` files beneath `${VCC_CACHE_DIR:-$HOME/.cache/vcc-history}/compiled/`. Do not run `xcc compile` merely to populate search results, and do not replace `xcc search` with raw `grep` or `rg`.
