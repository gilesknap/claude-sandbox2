# Claude in a sandbox — threat model and verification

This document spells out the defences `claude-sandbox` enforces, what
is deliberately exposed, and how to verify the installation at any
time. Lives in the meta-repo for documentation; the install does NOT
copy it into your workspace (do `cp /opt/claude-sandbox-src/README-CLAUDE.md ./`
yourself if you want it locally).

## Threat model

`claude-sandbox` defends a developer running Claude Code inside a
devcontainer against an LLM-driven attack — a hostile prompt, a
hostile file Claude reads, or a hostile tool result — attempting to
exfiltrate host credentials, drive the host IDE, or escalate
privileges.

Failure to establish a working sandbox **refuses installation** —
silent degradation to "Claude installed but not sandboxed" is itself
a UX failure mode that gets people pwned.

### In scope

- Host credentials reachable via `$HOME` dotfiles (`.gitconfig`,
  `.netrc`, `.Xauthority`, SSH keys, cloud SDK caches, etc.) —
  closed by strict-under-`/root` inversion: `--tmpfs /root` then
  bind back only `.claude` and `.cache`.
- Host credentials reachable via environment variables (`GH_TOKEN`,
  `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, `SSH_AUTH_SOCK`, …) — closed
  by `--clearenv` with an explicit allow-list.
- Docker/Compose secrets under `/run/secrets` — masked by
  `--tmpfs /run/secrets`.
- VS Code IPC bridges in `/tmp` (`vscode-ipc-*.sock`,
  `vscode-git-*.sock`) that would let a hostile prompt drive your
  host editor or trigger an OAuth login popup — masked by
  `--tmpfs /tmp`.
- X11 reachability via shared network namespace — `DISPLAY`
  scrubbed, `.Xauthority` masked with `/dev/null`.
- TIOCSTI terminal-injection back to the parent shell — blocked by
  `--new-session` (calls `setsid()`).
- Privilege escalation via `sudo` / `su` / `doas` — denied by zero
  capabilities (`--cap-drop ALL`) and `NO_NEW_PRIVS` (implicit in
  `bwrap`'s `exec`).

### Out of scope

- **Workspace contents.** Claude has to read your workspace to do
  its job — this is irreducible. Keep secrets outside the workspace
  (e.g. in `~/.config/` mounted via your devcontainer's `mounts`,
  not in the workspace itself).
- **The container host kernel.** A bwrap-aware kernel exploit is
  out of scope; `claude-sandbox` is a credential-isolation tool,
  not a sandbox against arbitrary native code execution.
- **Network egress filtering.** Claude needs network. The sandbox
  shares the network namespace and does not run a per-process
  firewall. If you need egress filtering, run the devcontainer
  itself behind one.
- **Non-standard host bind-mounts at credential paths.** The
  installer scans `mount` output and warns about
  `/kubeconfig`-style binds at install time, but it cannot
  enumerate every possible custom mount.
- **Non-root devcontainers** and **rootful Docker with default
  AppArmor**. v1 targets rootless podman + Debian/Ubuntu base
  images + `remoteUser=root`; the latter combinations are tracked
  for v2.

## What's locked down

Each defence below is cross-referenced with the corresponding
`/verify-sandbox` check number (run the slash command inside Claude,
or `claude-sandbox verify` from a shell, to verify them all).

| Defence | bwrap primitive | `/verify-sandbox` |
|---|---|---|
| Sandbox is actually entered | `IS_SANDBOX=1` sentinel | check 01 |
| bwrap is the parent process | `--unshare-pid` + exec | check 02 |
| Strict-under-`/root` by inversion | `--tmpfs /root` then bind `.claude` / `.cache` | check 03 |
| Host env vars scrubbed | `--clearenv` + explicit allow-list | checks 04, 05 |
| Zero capabilities | `--cap-drop ALL` | check 06 |
| PID namespace | `--unshare-pid` | check 07 |
| SysV IPC namespace | `--unshare-ipc` | check 08 |
| UTS namespace | `--unshare-uts` | check 09 |
| Network reachable (Claude needs it) | `--share-net` (NOT unshared) | check 10 |
| TIOCSTI terminal injection blocked | `--new-session` | check 11 |
| VS Code IPC bridges masked | `--tmpfs /tmp` | check 12 |
| User runtime dir masked | `--tmpfs /run/user` | check 13 |
| Docker/Compose secrets masked | `--tmpfs /run/secrets` | check 14 |
| `.gitconfig` defence in depth | `--bind-try /dev/null /root/.gitconfig` | check 15 |
| `.netrc` defence in depth | `--bind-try /dev/null /root/.netrc` | check 16 |
| `.Xauthority` defence in depth | `--bind-try /dev/null /root/.Xauthority` | check 17 |
| Curated gitconfig in effect | `GIT_CONFIG_GLOBAL=/etc/claude-gitconfig` | check 18 |

Implicit: `NO_NEW_PRIVS` (bwrap sets it before exec, so `sudo` /
setuid binaries are inert), `--die-with-parent` (the sandbox
disappears the moment Claude does).

## What's deliberately exposed

Anything not in the lockdown list above is reachable from inside
Claude. The deliberate exposures are:

- **Your workspace** (read/write). This is the whole point of
  Claude — see the visibility caveat below.
- **`/etc/claude-gitconfig`** (read). The curated gitconfig
  regenerated at every `install` with your host's current
  `user.name` / `user.email` and a `gh` credential helper for
  `https://github.com`.
- **`/root/.claude/`** (read/write). Claude's own state, settings,
  skills, and hooks.
- **`/root/.cache/`** (read/write, if present). Tool caches Claude
  needs across runs.
- **Network** (`--share-net`). Claude needs to reach
  `api.anthropic.com` and (if you use them) GitHub / GitLab over
  HTTPS.

## Workspace visibility caveat

Workspace contents are visible to Claude — this is irreducible.
Claude has to read your workspace to do its job. The sandbox
protects you against host-credential leaks via env vars, dotfiles,
and IPC sockets, but **anything you check out into your workspace
is by design reachable from Claude's tools**.

Practical rule: keep secrets outside the workspace (e.g., in
`~/.config/` mounted via your devcontainer's `mounts`). Don't put
`.env` files with production credentials at the workspace root and
expect them to be invisible.

## How to verify

From inside Claude, run:

```
/verify-sandbox
```

The command runs 18 PASS/FAIL checks against the live process and
prints a summary table. Any FAIL exits the command non-zero (so
you can use it as a CI assertion), and the FAIL line names which
defence regressed.

A pre-prompt hook (`.claude/hooks/sandbox-check.sh`) runs a
sub-second subset of these checks before every prompt and blocks
the prompt if the sandbox is not intact.

From a shell:

```
claude-sandbox verify
```

Same 18-check spec, same exit code semantics.

## What's installed

Container-scoped (regenerated by `claude-sandbox install` on every
run; lost on container rebuild and re-established by the
`postCreateCommand` bootstrap snippet documented in `README.md`):

- `/opt/claude/bin/claude` — the real Claude Code binary.
- `/usr/local/bin/claude` — a shadow that wraps the real binary in
  a `bwrap` sandbox. Falls through to the real binary when
  `IS_SANDBOX=1` so an internal `claude` invocation from a hook
  doesn't recurse.
- `/usr/local/bin/claude-sandbox` — typer CLI shim that re-execs
  into `uv run claude-sandbox` from `/opt/claude-sandbox-src`.
- `/etc/claude-gitconfig` — curated gitconfig.

Workspace (place-once, idempotent — never silently overwritten):

- `<workspace>/.claude/settings.json` — created from scratch if
  missing; one-key surgical merge of `hooks.UserPromptSubmit` only
  if pre-existing. No other settings key is ever touched.
- `<workspace>/.claude/hooks/sandbox-check.sh` — the
  `UserPromptSubmit` hook that gatekeeps every prompt.

Not placed by `install` (opt-in via separate commands or `git add`):

- Skills, commands — opt-in via `install-skill` / `install-command`.
- `CLAUDE.md`, `README-CLAUDE.md` — neither is placed in the user's
  workspace by `install`. They live in the meta-repo for
  dogfooding.

## Running it

```
claude
```

(or `claude-sandbox install` if you need to refresh the curated
gitconfig and shadow after a host gitconfig edit). The shadow on
`$PATH` always wraps; you cannot accidentally run the unwrapped
binary from your normal shell.

For forge authentication, use `gh auth login` (GitHub) or
`glab auth login` (GitLab) from inside the sandbox. Both run their
interactive flow against the credential helper the curated
gitconfig registers.
