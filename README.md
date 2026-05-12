[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

# claude-sandbox

bwrap-isolated Claude Code for Debian/Ubuntu devcontainers. Clone the
repo, run `sudo ./install`, and the shadow on `$PATH` wraps your
`claude` so a hostile prompt cannot reach host credentials, IDE
bridges, or your shell environment.

> The project is currently published as `gilesknap/claude-sandbox2`
> during the proving period. Once stable it reverts to the canonical
> `gilesknap/claude-sandbox` URL.

## Quickstart

Inside any Debian/Ubuntu devcontainer (running as `root`, typical
rootless-podman pattern):

```
git clone https://github.com/gilesknap/claude-sandbox2.git
cd claude-sandbox2
sudo ./install
```

That's it. The shadow `claude` on `$PATH` now wraps every invocation
in `bwrap`. Run `/verify-sandbox` from inside Claude to confirm the
16-check battery + 10 adversarial breakout probes pass.

The installer is idempotent: re-running after a devcontainer rebuild
re-establishes the shadow without re-downloading Claude.

## What you get

- A shadow `/usr/local/bin/claude` that auto-wraps the real Claude
  binary (parked at `~/.local/bin/claude`, where Anthropic's installer
  drops it) in a `bwrap` sandbox with `--ro-bind / /` + `--tmpfs $HOME`,
  `--clearenv`, `--cap-drop ALL`, `--unshare-pid/ipc/uts`,
  TIOCSTI defence via `script(1)`, and the rest of the threat model.
- A curated `/etc/claude-gitconfig` so `git push` works inside the
  sandbox via `gh` / `glab` as the credential helper. Regenerated on
  every launch from your host's current `user.name` / `user.email`.
- A workspace `.claude/settings.json` that wires a sub-second
  `UserPromptSubmit` hook (`sandbox-check.sh`) which refuses every
  prompt unless `IS_SANDBOX=1` is set — defence against the "user
  invoked Claude via a non-shadow path" bypass.
- **Refusal-on-failure**: if the host can't run unprivileged user
  namespaces, the installer refuses with a specific actionable
  diagnostic — never installs a non-functional sandbox.

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

Runs the 16 PASS/FAIL battery + 10 adversarial breakout probes against
the live process and exits non-zero on any FAIL. The spec lives at
`.claude/commands/verify-sandbox.md`.

## Upgrading

```
just upgrade
```

Equivalent to `git pull --ff-only && sudo bash install`.

## Authenticating with forges

```
just gh-auth
just glab-auth                  # gitlab.com
just glab-auth gitlab.diamond.ac.uk
```

Both walk you through a fine-grained-PAT prompt, feed the token to
the respective CLI's `auth login`, and unset the variable. Tokens
never enter shell history.

## Development

```
git clone https://github.com/gilesknap/claude-sandbox2.git
cd claude-sandbox2
just test
```

`just test` runs `bash tests/bwrap_argv.sh && bash tests/smoke.sh`.
No `uv sync`, no pytest, no twine — bash all the way down.

The repo's own `.claude/` IS the canonical source of shipped skills,
commands, and hooks — editing one updates both how Claude behaves on
this repo AND what the installer ships into target workspaces.

## License

See [`LICENSE`](./LICENSE).
