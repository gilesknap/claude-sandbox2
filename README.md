# claude-sandbox

bwrap-isolated Claude Code for rootless-podman + Debian/Ubuntu
devcontainers. One curl-bash inside an attached devcontainer wraps
your `claude` so a hostile prompt cannot reach host credentials, IDE
bridges, or your shell environment.

> The project is currently published as `gilesknap/claude-sandbox2`
> during the proving period. Once stable it reverts to the canonical
> `gilesknap/claude-sandbox` URL.

## Quickstart

Inside any Debian/Ubuntu rootless-podman devcontainer (running as
`root`):

```
curl -fsSL https://raw.githubusercontent.com/gilesknap/claude-sandbox2/main/install | bash
```

That's it. The shadow `claude` on `$PATH` now wraps every invocation
in `bwrap`. Run `/verify-sandbox` from inside Claude to confirm the
17-check battery passes.

## What you get

- A shadow `/usr/local/bin/claude` that auto-wraps the real Claude
  binary in a `bwrap` sandbox (`--ro-bind / /` + `--tmpfs /root`,
  `--clearenv`, `--cap-drop ALL`, `--unshare-pid/ipc/uts`,
  `--new-session`, `/run/secrets` masked, `$HOME` dotfiles masked
  with `/dev/null`).
- A typer CLI `claude-sandbox` with 7 commands (`install`, `verify`,
  `upgrade`, `list-skills`, `list-commands`, `install-skill`,
  `install-command`).
- A curated `/etc/claude-gitconfig` so `git push` works inside the
  sandbox via `gh` as the credential helper, with commits attributed
  to your host's `user.name` / `user.email`.
- A workspace `.claude/settings.json` that wires a sub-second
  `UserPromptSubmit` hook (`sandbox-check.sh`) which refuses every
  prompt unless `IS_SANDBOX=1` is set — defence against the "user
  invoked Claude via a non-shadow path" bypass.
- **Refusal-on-failure**: if the host can't run unprivileged user
  namespaces (rootful Docker default AppArmor, sysctls disabling
  userns, etc.), the installer refuses with a specific actionable
  diagnostic — never installs a non-functional sandbox.

## Surviving devcontainer rebuilds

Container-scoped artifacts (`/usr/local/bin/claude`, `/opt/claude/bin/claude`,
`/etc/claude-gitconfig`) disappear on every rebuild. To re-establish
them automatically, commit a 5-line bootstrap script and a
1-line `postCreateCommand`:

`.devcontainer/bootstrap.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
if ! command -v claude-sandbox >/dev/null 2>&1; then
    curl -fsSL https://raw.githubusercontent.com/gilesknap/claude-sandbox2/main/install | bash
fi
```

`devcontainer.json` (add this key):

```json
"postCreateCommand": "bash .devcontainer/bootstrap.sh"
```

## Make permanent vs keep temporary

The installer drops `<workspace>/.claude/{settings.json,hooks/sandbox-check.sh}`.

**Make permanent** — commit them:

```
git add .claude/ .devcontainer/bootstrap.sh
git commit -m "Add claude-sandbox bootstrap"
```

**Keep temporary** — gitignore them. Paste this into `.gitignore`:

```
.claude/settings.json
.claude/hooks/
```

There's no `graduate` command because none is needed — `git add` IS
the graduation flow.

## Opt-in skills and commands

Browse what's shipped:

```
claude-sandbox list-skills
claude-sandbox list-commands
```

Install one or more (defaults to `<workspace>/.claude/`):

```
claude-sandbox install-skill diagnose tdd
claude-sandbox install-command grill-me memo
```

Globs and bundles:

```
claude-sandbox install-skill 'pocock-*'
claude-sandbox install-skill --bundle pocock
claude-sandbox install-command --all
```

Refuses on conflict if the workspace already has a different version;
re-run with `--force` to overwrite.

## Threat model

See [`README-CLAUDE.md`](./README-CLAUDE.md). TL;DR: in scope are
host credentials reachable via `$HOME` dotfiles, env vars,
`/run/secrets`, VS Code IPC sockets in `/tmp`, X11 reachability, and
TIOCSTI terminal injection. Out of scope are workspace contents
(Claude has to read your workspace) and arbitrary kernel exploits.

## Verifying

```
/verify-sandbox        # inside Claude
```

Runs the 17 PASS/FAIL battery against the live process and exits
non-zero on any FAIL. The spec lives at
`.claude/commands/verify-sandbox.md` — a structured battery catches
known-defence regressions; the real assurance is whether a working
Claude session can find a breakout, which a checklist can't measure.

## Upgrading

```
claude-sandbox upgrade
```

`git pull`s the source clone at `/opt/claude-sandbox-src`, re-syncs
the venv, and re-execs `install`.

## Development

Clone, sync, run the tests:

```
git clone https://github.com/gilesknap/claude-sandbox2.git
cd claude-sandbox2
uv sync
uv run pytest
uv run ruff check src/ tests/
```

The repo's own `.claude/` IS the canonical source of shipped skills
and commands — editing one updates both how Claude behaves on this
repo AND what `install-skill` ships in one move. No `share/` staging
directory, no symlinks.

## License

MIT. See [`LICENSE`](./LICENSE).
