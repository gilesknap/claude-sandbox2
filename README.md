[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

# claude-sandbox

bwrap-isolated Claude Code for Debian/Ubuntu devcontainers. A hostile
prompt, file, or tool result cannot reach your host credentials, IDE
bridges, or shell environment.

## Quickstart

Inside any Debian/Ubuntu devcontainer (running as `root`, typical
rootless-podman pattern):

```
git clone https://github.com/gilesknap/claude-sandbox2.git
cd claude-sandbox2
./install
```

Then run `claude` as usual — the shadow on `$PATH` wraps every
invocation in `bwrap`. Run `/verify-sandbox` from inside a session
to confirm the 16-check battery + 10 adversarial breakout probes
pass.

The installer is idempotent: re-run after a devcontainer rebuild and
the shadow is re-established without re-downloading Claude. Wire
`bash <clone>/install` into your devcontainer's `postCreate.sh` to
automate that step.

> Currently published as `gilesknap/claude-sandbox2` during the
> proving period. Once stable it reverts to the canonical
> `gilesknap/claude-sandbox` URL.

### Devcontainers using terminal-config (e.g. python-copier-template)

If your devcontainer bind-mounts `~/.config/terminal-config` at
`/user-terminal-config` (the `python-copier-template` convention),
clone there instead:

```
cd /user-terminal-config
git clone https://github.com/gilesknap/claude-sandbox2.git
cd claude-sandbox2
./install
```

The clone lives on the host under `~/.config/terminal-config`, so it
survives devcontainer rebuilds and is reusable from every
devcontainer that mounts the same terminal-config dir — one clone,
one `just upgrade` cadence, every project sandboxed.

## What you get

- A shadow `/usr/local/bin/claude` that auto-wraps the real Claude
  binary (relocated to `/usr/libexec/claude-sandbox/claude` so it
  sits off the user's PATH — Anthropic's installer drops it at
  `~/.local/bin/claude` and prepends `~/.local/bin` to your shell rc,
  which would otherwise let plain `claude` bypass the shadow) in a
  `bwrap` sandbox with `--ro-bind / /` + `--tmpfs $HOME`,
  `--clearenv`, `--cap-drop ALL`, `--unshare-pid/ipc/uts`, TIOCSTI
  defence via `script(1)`, and the rest of the threat model.
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

Runs the 16 PASS/FAIL battery + 10 adversarial breakout probes
against the live process and exits non-zero on any FAIL. The spec
lives at `.claude/commands/verify-sandbox.md`.

## Upgrading

```
just upgrade
```

Equivalent to `git pull --ff-only && bash install`.

## Promoting into a host workspace

`just promote` makes a target workspace a self-sufficient claude-sandbox
host — a teammate who clones the target only needs the devcontainer to
come up; the installer runs from `postCreate.sh` and the curated
`.claude/` is in tree.

```
just promote                       # promote into $PWD
just promote /workspaces/fastcs    # promote into the named target
```

Three things land in the target:

1. **Curated `.claude/`** — commands, skills, hooks, statusline; plus a
   surgical merge of our `sandbox-check.sh` hook + `statusLine` into
   `<target>/.claude/settings.json` (pre-existing keys preserved).
2. **Install machinery** — `.devcontainer/claude-sandbox/{install.sh,
   claude-shadow, promote.sh}`, so postCreate can run install.sh
   directly. The root `install` shim is *not* copied; it's the source
   repo's manual-UX entry and not a primary workflow for targets. The
   cost is ~3 small bash files per promoted repo; re-running
   `just promote` from this clone re-syncs byte-equal.
3. **`.devcontainer/postCreate.sh`** running
   `bash .devcontainer/claude-sandbox/install.sh` — created if absent,
   idempotently appended otherwise.

After it finishes, promote prints a one-line `"postCreateCommand"`
snippet to paste into the target's `.devcontainer/devcontainer.json`.
We deliberately don't auto-edit that file: it's JSONC in the wild,
structured editing while preserving comments is more code than this
repo wants, and you're the one who knows whether you've already wired
it or need to combine with an existing `postCreateCommand`. One-time
edit; subsequent `just promote` runs are byte-stable.

`just promote` is idempotent, refuses self-targeting (`TARGET == clone`),
and does NOT touch `~/.claude` — that channel stays reserved for
cross-container shared state (OAuth, memories).

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

`just test` runs `bash tests/bwrap_argv.sh` and a sandboxed
`bash tests/smoke.sh`. No `uv sync`, no pytest, no twine — bash all
the way down.

The repo's own `.claude/` IS the canonical source of shipped skills,
commands, and hooks — editing one updates both how Claude behaves on
this repo AND what the installer ships into target workspaces.

## License

See [`LICENSE`](./LICENSE).
