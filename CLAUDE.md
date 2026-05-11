# claude-sandbox: working notes for Claude

This file documents project conventions for Claude when running on
this repo. The PRD (`PRD.md`) is canonical; this file is the fast
shortcut.

## What this project is

A bwrap-isolated wrapper for Claude Code that installs into Debian/Ubuntu
devcontainers. The shadow `/usr/local/bin/claude` wraps the real
binary in `bwrap` with strict-under-`/root` inversion, `--clearenv`,
`--cap-drop ALL`, and the rest of the threat model documented in
`README-CLAUDE.md`.

## Where things live

| Path | What |
|---|---|
| `install` | bash, curl-bashable entry point. Probes, installs apt deps + uv + Claude, clones into `/opt/claude-sandbox-src`, re-execs `claude-sandbox install`. |
| `src/claude_sandbox/cli.py` | typer entry point — 7 subcommands. |
| `src/claude_sandbox/installer.py` | Orchestrator. Probes -> places container artifacts -> places workspace artifacts. Idempotent. |
| `src/claude_sandbox/probe.py` | apt detect, userns probe, mount-scan warnings. Refusal-paths raise typed exceptions. |
| `src/claude_sandbox/settings_merger.py` | One-key surgical merger for `hooks.UserPromptSubmit`. Never touches any other key. |
| `src/claude_sandbox/skill_installer.py` | `install-skill` / `install-command`. Glob expansion, `--all`, `--bundle`, copy-if-missing, refuse-if-different. |
| `src/claude_sandbox/gitconfig.py` | Atomic write of `/etc/claude-gitconfig`. |
| `src/claude_sandbox/verifier.py` | Headless 18-check runner. Extracts bash bodies from the `verify-sandbox.md` spec and runs them. |
| `src/claude_sandbox/data/claude-shadow` | bash. The shadow `claude` binary template. Substituted by the installer. |
| `src/claude_sandbox/data/bwrap_argv.sh` | bash. Pure function emitting the bwrap argv. Sourced by the shadow. **Bash, not Python — runs on every `claude` launch and the latency budget is < 50 ms.** |
| `src/claude_sandbox/data/bundles.toml` | Bundle definitions for `--bundle NAME`. |
| `.claude/skills/` | Canonical shipped skills. Editing one updates dogfooded behaviour AND `install-skill`'s ship in one move. No `share/` indirection, no symlinks. |
| `.claude/commands/` | Canonical shipped commands. Same as skills. |
| `.claude/hooks/sandbox-check.sh` | The `UserPromptSubmit` hook the installer copies into workspaces. |
| `tests/` | pytest, plus `tests/bwrap_argv.sh` as the bash spec for the bwrap argv builder. |

## How to run tests

```
uv sync
uv run pytest -x          # all 100+ tests
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
bash tests/bwrap_argv.sh  # the bash spec, also pytest-wrapped
```

## Project conventions

- **`from __future__ import annotations`** at the top of every module.
- **Modern PEP-604 unions** (`int | None`, not `Optional[int]`).
- **Comments lead with WHY**, not what. The mechanical "what" is
  visible in the code; the rationale isn't.
- **Container-mode v1 paths only** — `/opt/claude-sandbox-src`,
  `/usr/local/bin/claude`, `/opt/claude/bin/claude`,
  `/etc/claude-gitconfig`. v2's host-mode parameterisation is filed
  as a separate issue.
- **No emojis in code or commit messages.**
- **No `share/` / `lib/` directories.** The package is `src/claude_sandbox/`;
  bash hot-path artifacts are package data; the canonical `.claude/`
  is at the repo root.

## What stays bash, what's Python

| File | Language | Why |
|---|---|---|
| `install` | bash | Curl-bashable; cannot assume any interpreter beyond bash itself. |
| `claude-shadow` | bash | Runs on every `claude` invocation; latency budget < 50 ms. |
| `bwrap_argv.sh` | bash | Sourced by the shadow on the launch hot path. |
| `sandbox-check.sh` | bash | `UserPromptSubmit` hook fires on every prompt; bash is sub-millisecond, Python is ~30 ms. |
| Everything else | Python | Install-time orchestration with no latency sensitivity. |

## Running the sandbox to dogfood

After `bash install` inside a fresh devcontainer:

```
claude                    # always sandboxed via the /usr/local/bin/claude shadow
claude-sandbox verify     # 18 PASS, exit 0
/verify-sandbox           # same, but from inside Claude
```

## Editing a shipped skill or command

The shipped artifacts live at `.claude/skills/<name>/SKILL.md` and
`.claude/commands/<name>.md`. Editing one updates both:

1. The dogfooded behaviour (Claude on this repo loads `.claude/`
   directly).
2. What `claude-sandbox install-skill <name>` ships to a user's
   workspace (the CLI reads from the same `.claude/` in the cloned
   source).

So one edit, two effects, no symlinks.

## Adding a new shipped skill / command

1. Drop `.claude/skills/<name>/SKILL.md` (with valid YAML frontmatter:
   `name:` + `description:`) — for skills.
2. Drop `.claude/commands/<name>.md` — for commands.
3. Optionally add it to a bundle in `src/claude_sandbox/data/bundles.toml`.
4. Run `uv run pytest -k shipped_payload` to confirm the listing
   tests still pass.

## Where the threat model lives

`README-CLAUDE.md`. The 18 verify-sandbox checks (in
`.claude/commands/verify-sandbox.md`) are the executable spec — if
you add a defence, add a check.
