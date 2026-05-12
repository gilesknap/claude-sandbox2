# claude-sandbox: working notes for Claude

This file documents project conventions for Claude when running on
this repo. The threat model and sandbox model live in
`README-CLAUDE.md`; the executable spec for the sandbox checks lives
in `.claude/commands/verify-sandbox.md`.

## What this project is

A bwrap-isolated wrapper for Claude Code that installs into Debian/Ubuntu
devcontainers. The shadow `/usr/local/bin/claude` wraps the real
binary in `bwrap` with strict-under-`/root` inversion, `--clearenv`,
`--cap-drop ALL`, and the rest of the threat model documented in
`README-CLAUDE.md`.
