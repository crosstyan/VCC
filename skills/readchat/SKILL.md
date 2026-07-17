---
name: readchat
description: "Compile and read a specific Claude Code, Codex, OMP, T3 Code, or Reasonix session. Use when the user mentions a session file, T3 thread ref or state.sqlite, or asks to review a past chat."
---

# Read a Specific Conversation

Use the installed `xcc` command.

1. Use the session path or T3 thread ref supplied by the user or printed by `xcc list`.
2. Compile it explicitly:

   ```bash
   xcc compile "<session-path-or-t3-ref>"
   ```

   This writes `.txt` and `.min.txt` beside the source by default. Use `-o <directory>` only when the user requests a separate output location.

3. Read the highest-numbered `.min.txt` chunk first. Higher suffixes are newer after Claude Code, Codex, or OMP compaction.
4. Follow any full-view range with:

   ```bash
   xcc read "/path/to/session.txt:START-END"
   ```

5. Use `.txt` as the canonical transcript and line-number coordinate system. Leave compiled output in place unless the user asks to remove it.
