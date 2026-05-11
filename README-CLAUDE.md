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
  bind back only `.claude`, `.claude.json`, `.cache`, and the narrow
  forge-CLI allowlist `.config/gh` / `.config/glab-cli` (see
  "What's deliberately exposed").
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
`/verify-sandbox` check number (run the slash command inside Claude
to verify them all).

| Defence | bwrap primitive | `/verify-sandbox` |
|---|---|---|
| Sandbox is actually entered | `IS_SANDBOX=1` sentinel | check 01 |
| Setuid escalation blocked | `NO_NEW_PRIVS` (set by bwrap before exec) | check 02 |
| Strict-under-`/root` by inversion | `--tmpfs /root` then bind `.claude` / `.claude.json` / `.cache` / `.config/{gh,glab-cli}` | check 03 |
| Host env vars scrubbed | `--clearenv` + explicit allow-list | checks 04, 05 |
| Zero capabilities | `--cap-drop ALL` | check 06 |
| PID namespace (kill/ptrace scoping) | `--unshare-pid` | check 07 |
| SysV IPC namespace | `--unshare-ipc` | check 08 |
| UTS namespace | `--unshare-uts` | check 09 |
| TIOCSTI terminal injection blocked | `--new-session` | check 10 |
| VS Code IPC bridges masked | `--tmpfs /tmp` | check 11 |
| User runtime dir masked | `--tmpfs /run/user` | check 12 |
| Docker/Compose secrets masked | `--tmpfs /run/secrets` | check 13 |
| `.gitconfig` defence in depth | `--bind-try /dev/null /root/.gitconfig` | check 14 |
| `.netrc` defence in depth | `--bind-try /dev/null /root/.netrc` | check 15 |
| `.Xauthority` defence in depth | `--bind-try /dev/null /root/.Xauthority` | check 16 |
| Curated gitconfig in effect | `GIT_CONFIG_GLOBAL=/etc/claude-gitconfig` | check 17 |

Network egress (`--share-net`, NOT unshared) is deliberately open so
Claude can reach `api.anthropic.com`. It has no PASS/FAIL check —
`--share-net` is a non-defence, and any regression makes Claude fail
on first use rather than silently.

Implicit: `--die-with-parent` (the sandbox disappears the moment
Claude does).

### Procfs view: what `--unshare-pid` does NOT deliver on rootless devcontainers

`--unshare-pid` reliably gives kernel-level pidns isolation (the
sandbox cannot `kill()` or `ptrace()` host or devcontainer processes
— check 07 verifies this via `/proc/self/status:NSpid:`). The
companion property — `/proc` reflecting *only* the sandbox's own
process tree — depends on bwrap successfully mounting procfs against
the new pidns. On rootless nested-userns hosts (the standard VS Code
devcontainer pattern, where the outer container has no host
`CAP_SYS_ADMIN`), bwrap mounts procfs against its *outer* pidns
instead, so the sandbox's `/proc` enumerates host PIDs. We tested
`unshare(1) --user --pid --fork --mount-proc --map-root-user` as a
prefix to bwrap and it `EPERM`s on `mount("proc")` for the same
underlying reason: kernel-locked parent mount.

Implication for the threat model: this is **information disclosure**
(Claude can see the user's process tree and command lines), **not
credential exfil**. The credential-bearing procfs entries —
`/proc/<pid>/environ`, `/maps`, `/fd`, `/mem`, `/cwd` — are gated by
`PTRACE_MODE_READ_FSCREDS`, which under YAMA `ptrace_scope=1` (the
Ubuntu/Debian default and what every devcontainer base image ships)
is restricted to the caller's descendants. The sandbox has no
descendant relationship with VS Code, terminal sessions, or other
devcontainer processes, so those reads `EACCES`.

The launch-time probe in `claude-shadow` detects whether procfs is
properly aligned with the new pidns (it tests `$$ ==
/proc/self/status:Pid:` inside a probe bwrap) and exports
`CLAUDE_SANDBOX_FRESH_PROC=0/1` accordingly. On hosts where it works
(Linux desktops with full bwrap privileges) check 07 plus the
process-tree view are both clean. On rootless devcontainers check 07
still passes (kernel pidns isolation is intact) and a one-line
warning is printed on stderr at launch.

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
- **`/root/.claude.json`** (read/write). Claude Code's account-level
  state file — OAuth token, recent-projects list, settings. A
  top-level file rather than a directory, so it needs its own bind
  on top of `~/.claude/`. Without this, the strict-under-/root tmpfs
  would swallow the auth token on every launch and you'd re-login
  on each invocation.
- **`/root/.cache/`** (read/write, if present). Tool caches Claude
  needs across runs.
- **`/root/.config/gh/`** (read/write, if present). The `gh` CLI's
  token store. Bound through so `gh auth status` works inside the
  sandbox and the curated gitconfig's `gh auth git-credential`
  helper can authenticate `git push` to GitHub without an OAuth
  popup.
- **`/root/.config/glab-cli/`** (read/write, if present). The `glab`
  CLI's token store. Bound through for the same reason as `gh`.
  Sibling paths under `/root/.config/` (VS Code state, other cred
  helpers, etc.) are NOT bound — only these two subdirs.
- **Network** (`--share-net`). Claude needs to reach
  `api.anthropic.com` and (if you use them) GitHub / GitLab over
  HTTPS. Because the network namespace is shared with the host,
  Claude can also enumerate the host's interface addresses, routing
  table, and DNS resolver via `AF_NETLINK` / standard tooling
  (`ip addr`, `ip route`, `/etc/resolv.conf`). This is
  network-identity disclosure, not credential exfil — but it means
  the sandbox is visible to internal services on the same host
  network. Don't run a local `metadata-style` credential service on
  the loopback or RFC1918 of a host that also runs `claude` unless
  you're OK with Claude reaching it. /verify-sandbox flags this as
  an `[INCONCLUSIVE]` adversarial probe so it stays on the radar.

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

The command runs 17 PASS/FAIL checks against the live process and
prints a summary table. Any FAIL exits the command non-zero (so
you can use it as a CI assertion), and the FAIL line names which
defence regressed.

A pre-prompt hook (`.claude/hooks/sandbox-check.sh`) runs a
sub-second subset of these checks before every prompt and blocks
the prompt if the sandbox is not intact.

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
