# claude-sandbox: working notes for Claude

This file documents project conventions for Claude when running on
this repo. The threat model and sandbox model live in
`README-CLAUDE.md`; the executable spec for the sandbox checks lives
in `.claude/commands/verify-sandbox.md`.

## What this project is

A bwrap-isolated wrapper for Claude Code that installs into
Debian/Ubuntu devcontainers. Bash-only — no Python package, no uv,
no pytest. Clone the repo, run `sudo ./install`, and the shadow at
`/usr/local/bin/claude` wraps every `claude` invocation in `bwrap`
with strict-under-`$HOME` inversion, `--clearenv`, `--cap-drop ALL`,
and the rest of the threat model documented in `README-CLAUDE.md`.

## Layout

- `install` — root shim. Refuses non-root and execs
  `.devcontainer/claude-sandbox/install.sh`.
- `.devcontainer/claude-sandbox/install.sh` — the real installer.
  Apt-installs deps, curl-installs Claude, drops the shadow,
  wires `<workspace>/.claude/settings.json`, copies the hook.
- `.devcontainer/claude-sandbox/claude-shadow` — self-contained
  shadow. `bwrap_argv_build` is inlined as a bash function in the
  same file so the argv contract is testable in isolation
  (`tests/bwrap_argv.sh` sources the shadow with
  `CLAUDE_SHADOW_SOURCE_ONLY=1`).
- `justfile` — `test`, `upgrade`, `gh-auth`, `glab-auth`, `verify`.
- `tests/bwrap_argv.sh` + `tests/smoke.sh` — the entire test surface.
- `.claude/` — distribution scope: skills, commands, hooks. Promoted
  wholesale into target workspaces.
