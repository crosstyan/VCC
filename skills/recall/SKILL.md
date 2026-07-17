---
name: recall
description: "Recover context from previous Claude Code, Codex, OMP, T3 Code, or Reasonix sessions. Use when the user asks to recall previous work or continue a prior conversation."
---

# Recall Previous Work

Use the installed `xcc` command.

1. List recent current-project main sessions:

   ```bash
   xcc list --harness all --cwd "$PWD" --limit 10
   ```

   Choose the newest relevant prior session; do not blindly select the active conversation.

2. If the missing context may have lived in a delegated Claude Code task, retry with:

   ```bash
   xcc list --harness all --cwd "$PWD" --limit 10 --include-subagents
   ```

3. If no current-project record is useful, repeat without `--cwd`, keeping main sessions first and adding `--include-subagents` only as a retry.
4. Compile the selected path or T3 thread ref exactly as listed:

   ```bash
   xcc compile "<session-path-or-t3-ref>"
   ```

5. Read the highest-numbered `.min.txt` chunk first, then follow precise ranges with:

   ```bash
   xcc read "/path/to/session.txt:START-END"
   ```

6. Summarize the recovered goal, decisions, completed work, open work, and blockers. Verify referenced files and repository state directly before relying on historical claims.
