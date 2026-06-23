# Installation Guide for Codex, OMP, and Reasonix

## Requirements

- Python 3.10+
- uv
- Codex, OMP, and/or Reasonix

## Install

Keep this repo at a stable path, then install the uv project:

```bash
cd /home/crosstyan/Code/VCC
uv sync
```

The skills invoke the CLI with:

```bash
uv --project "${VCC_ROOT:-/home/crosstyan/Code/VCC}" run xcc
```

If the repo lives somewhere else, set `VCC_ROOT` for the harness process.

Install the skills by symlinking these four directories into each harness skill directory:

- `skills/conversation-compiler`
- `skills/readchat`
- `skills/recall`
- `skills/searchchat`

Codex:

```bash
mkdir -p ~/.codex/skills
ln -sfn /home/crosstyan/Code/VCC/skills/conversation-compiler ~/.codex/skills/conversation-compiler
ln -sfn /home/crosstyan/Code/VCC/skills/readchat ~/.codex/skills/readchat
ln -sfn /home/crosstyan/Code/VCC/skills/recall ~/.codex/skills/recall
ln -sfn /home/crosstyan/Code/VCC/skills/searchchat ~/.codex/skills/searchchat
```

OMP:

```bash
OMP_SKILLS_DIR="${PI_CODING_AGENT_DIR:-$HOME/.omp/agent}/skills"
mkdir -p "$OMP_SKILLS_DIR"
ln -sfn /home/crosstyan/Code/VCC/skills/conversation-compiler "$OMP_SKILLS_DIR/conversation-compiler"
ln -sfn /home/crosstyan/Code/VCC/skills/readchat "$OMP_SKILLS_DIR/readchat"
ln -sfn /home/crosstyan/Code/VCC/skills/recall "$OMP_SKILLS_DIR/recall"
ln -sfn /home/crosstyan/Code/VCC/skills/searchchat "$OMP_SKILLS_DIR/searchchat"
```

Reasonix:

```bash
REASONIX_SKILLS_DIR="${REASONIX_HOME:-$HOME/.reasonix}/skills"
mkdir -p "$REASONIX_SKILLS_DIR"
ln -sfn /home/crosstyan/Code/VCC/skills/conversation-compiler "$REASONIX_SKILLS_DIR/conversation-compiler"
ln -sfn /home/crosstyan/Code/VCC/skills/readchat "$REASONIX_SKILLS_DIR/readchat"
ln -sfn /home/crosstyan/Code/VCC/skills/recall "$REASONIX_SKILLS_DIR/recall"
ln -sfn /home/crosstyan/Code/VCC/skills/searchchat "$REASONIX_SKILLS_DIR/searchchat"
```

Restart the harness so it picks up the skills.

## Update

Pull or edit the repo, then run `uv sync` if dependencies changed. Symlinked skill installs do not need to be recopied.

## Verify

Run:

```bash
uv --project /home/crosstyan/Code/VCC run xcc --help
uv --project /home/crosstyan/Code/VCC run xcc list --harness all --limit 5
```

Then ask Codex, OMP, or Reasonix to use `recall` or `searchchat`.

## Uninstall

Delete these symlinks from `~/.codex/skills/`, `${PI_CODING_AGENT_DIR:-~/.omp/agent}/skills`, and/or `${REASONIX_HOME:-~/.reasonix}/skills`:

- `conversation-compiler`
- `readchat`
- `recall`
- `searchchat`

Restart the harness after uninstalling.
