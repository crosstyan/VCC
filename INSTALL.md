# Installation Guide for Claude Code, Codex, OMP, T3 Code History, and Reasonix

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Claude Code, Codex, OMP, and/or Reasonix
- T3 Code is optional; VCC reads its history from SQLite when `${T3CODE_HOME:-$HOME/.t3}` exists.

## Install the CLI

Keep this repository at a stable path because the editable tool installation points back to the checkout. From the repository root, run:

```bash
uv tool install --editable .
xcc --help
```

The skills call the installed `xcc` command directly, and the compiler ships inside the installed package. No checkout path or compiler-path environment override is needed at runtime.

## Install the Skills

From the repository root, define the shared skill list and installer in your shell:

```bash
REPO_ROOT=$(pwd -P)
VCC_SKILLS="conversation-compiler readchat recall searchchat"

install_vcc_skills() {
  local skills_dir=$1
  local destination
  mkdir -p "$skills_dir"
  for skill in $VCC_SKILLS; do
    destination="$skills_dir/$skill"
    if [ -e "$destination" ] && [ ! -L "$destination" ]; then
      printf 'Refusing to replace existing non-symlink: %s\n' "$destination" >&2
      return 1
    fi
    ln -sfn "$REPO_ROOT/skills/$skill" "$destination"
  done
}
```

The installer refuses to overwrite a real file or directory. If a destination already exists as a non-symlink, move or remove it explicitly, then rerun the installer.

Then install the four skill directories into each harness you use.

### Claude Code

Claude Code user skills live under `CLAUDE_CONFIG_DIR` when it is set, otherwise under `$HOME/.claude`:

```bash
install_vcc_skills "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
```

### Codex

```bash
install_vcc_skills "${CODEX_HOME:-$HOME/.codex}/skills"
```

### OMP

```bash
install_vcc_skills "${PI_CODING_AGENT_DIR:-$HOME/.omp/agent}/skills"
```

### Reasonix

```bash
install_vcc_skills "${REASONIX_HOME:-$HOME/.reasonix}/skills"
```

Restart each harness after installing its skills.

T3 Code does not need skill installation for history search. `xcc list --harness t3code` reads `${T3CODE_HOME:-$HOME/.t3}/userdata/state.sqlite` and `${T3CODE_HOME:-$HOME/.t3}/dev/state.sqlite` when present. Override discovery with `VCC_T3CODE_DB=/path/to/state.sqlite`.

## Update

Pull or edit the checkout. Because the tool is installed editable, source and skill changes take effect immediately. If package metadata or dependencies changed, refresh the tool environment from the repository root:

```bash
uv tool install --editable . --force
```

Symlinked skill installs do not need to be recopied.

## Verify

Run:

```bash
xcc --help
xcc list --harness all --limit 5
xcc list --harness claude --limit 5
xcc list --harness t3code --limit 5
xcc list --harness reasonix --limit 5
```

Main-session transcripts are listed by default. If you also want to verify nested delegated (subagent) discovery, run:

```bash
xcc list --harness claude --limit 5 --include-delegated
```

Then ask Claude Code, Codex, OMP, or Reasonix to use `recall` or `searchchat`.

## Uninstall

Remove the tool:

```bash
uv tool uninstall vcc-history
```

Delete the four skill symlinks from the harness skill directories where you installed them:

- `conversation-compiler`
- `readchat`
- `recall`
- `searchchat`

Restart the harness after uninstalling its skills.
