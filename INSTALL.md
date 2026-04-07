# Installation Guide for Codex

## Requirements

- Python 3.10+
- Codex installed and using the default user skill path `~/.codex/skills`

## Install

Clone this repo to any temporary location.

Copy these four directories into `~/.codex/skills/`:

- `skills/conversation-compiler`
- `skills/readchat`
- `skills/recall`
- `skills/searchchat`

The result should be:

```text
~/.codex/skills/conversation-compiler
~/.codex/skills/readchat
~/.codex/skills/recall
~/.codex/skills/searchchat
```

Then restart Codex so it picks up the new skills.

## Update

Clone the repo again, replace those same four directories under `~/.codex/skills/`, and restart Codex.

## Verify

Run:

```bash
python "~/.codex/skills/conversation-compiler/scripts/find_codex_rollouts.py" --latest
```

Then ask Codex to use `recall` or `searchchat`.

## Uninstall

Delete these directories from `~/.codex/skills/`:

- `conversation-compiler`
- `readchat`
- `recall`
- `searchchat`

Restart Codex after uninstalling.
