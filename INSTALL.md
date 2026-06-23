# Installation Guide for Codex, OMP, and T3 Code History

## Requirements

- Python 3.10+
- uv
- Codex and/or OMP
- T3 Code is optional; VCC reads its history from SQLite when `${T3CODE_HOME:-~/.t3}` exists.

## Install

Keep this repo at a stable path, then install the uv project:

```bash
cd /Users/crosstyan/Code/VCC
uv sync
```

The skills invoke the CLI with:

```bash
uv --project "${VCC_ROOT:-/Users/crosstyan/Code/VCC}" run xcc
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
ln -sfn /Users/crosstyan/Code/VCC/skills/conversation-compiler ~/.codex/skills/conversation-compiler
ln -sfn /Users/crosstyan/Code/VCC/skills/readchat ~/.codex/skills/readchat
ln -sfn /Users/crosstyan/Code/VCC/skills/recall ~/.codex/skills/recall
ln -sfn /Users/crosstyan/Code/VCC/skills/searchchat ~/.codex/skills/searchchat
```

OMP:

```bash
OMP_SKILLS_DIR="${PI_CODING_AGENT_DIR:-$HOME/.omp/agent}/skills"
if [ -d "$(dirname "$OMP_SKILLS_DIR")" ]; then
  mkdir -p "$OMP_SKILLS_DIR"
  ln -sfn /Users/crosstyan/Code/VCC/skills/conversation-compiler "$OMP_SKILLS_DIR/conversation-compiler"
  ln -sfn /Users/crosstyan/Code/VCC/skills/readchat "$OMP_SKILLS_DIR/readchat"
  ln -sfn /Users/crosstyan/Code/VCC/skills/recall "$OMP_SKILLS_DIR/recall"
  ln -sfn /Users/crosstyan/Code/VCC/skills/searchchat "$OMP_SKILLS_DIR/searchchat"
fi
```

Restart the harness so it picks up the skills.

T3 Code does not need skill installation for history search. `xcc --harness t3code` reads `${T3CODE_HOME:-~/.t3}/userdata/state.sqlite` and `${T3CODE_HOME:-~/.t3}/dev/state.sqlite` if present. Override with `VCC_T3CODE_DB=/path/to/state.sqlite`.

## Update

Pull or edit the repo, then run `uv sync` if dependencies changed. Symlinked skill installs do not need to be recopied.

## Verify

Run:

```bash
uv --project /Users/crosstyan/Code/VCC run xcc --help
uv --project /Users/crosstyan/Code/VCC run xcc list --harness all --limit 5
uv --project /Users/crosstyan/Code/VCC run xcc list --harness t3code --limit 5
```

Then ask Codex or OMP to use `recall` or `searchchat`.

## Uninstall

Delete these symlinks from `~/.codex/skills/` and/or `${PI_CODING_AGENT_DIR:-~/.omp/agent}/skills`:

- `conversation-compiler`
- `readchat`
- `recall`
- `searchchat`

Restart the harness after uninstalling.
