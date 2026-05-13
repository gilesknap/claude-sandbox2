# Claude in a sandbox — threat model and verification

This document spells out the defences `claude-sandbox` enforces, what
is deliberately exposed, and how to verify the installation at any
time. Lives in the meta-repo for documentation; the install does NOT
copy it into your workspace (`cp <clone>/README-CLAUDE.md ./`
yourself if you want it locally).

## TL;DR

Each row maps a defence to the bwrap primitive that enforces it and
the `/verify-sandbox` check number that proves it. Run
`/verify-sandbox` from inside Claude to execute the full battery (16
PASS/FAIL checks + 10 adversarial breakout probes; the command exits
non-zero on any FAIL, so it's usable as a CI assertion).

| Defence | bwrap primitive | Verify |
|---|---|---|
| Sandbox is actually entered | `IS_SANDBOX=1` sentinel | check 01 |
| Setuid escalation blocked | `NO_NEW_PRIVS` (set by bwrap before exec) | check 02 |
| Strict-under-`/root` by inversion | `--tmpfs /root` then re-bind `.claude` / `.claude.json` / `.cache` / `.config/{gh,glab-cli}` | check 03 |
| Host env vars scrubbed | `--clearenv` + explicit allow-list | checks 04, 05 |
| Zero capabilities | `--cap-drop ALL` | check 06 |
| PID namespace (kill/ptrace scoping) | `--unshare-pid` | check 07 |
| SysV IPC namespace | `--unshare-ipc` | check 08 |
| UTS namespace | `--unshare-uts` | check 09 |
| TIOCSTI terminal injection blocked | `--dev /dev` + `script(1)` pty wrap | check 10 |
| VS Code IPC bridges masked | `--tmpfs /tmp` | check 11 |
| User runtime dir masked | `--tmpfs /run/user` | check 12 |
| Docker/Compose secrets masked | `--tmpfs /run/secrets` | check 13 |
| `.netrc` defence in depth | `--bind-try /dev/null /root/.netrc` | check 14 |
| `.Xauthority` defence in depth | `--bind-try /dev/null /root/.Xauthority` | check 15 |
| Curated gitconfig in effect | `GIT_CONFIG_GLOBAL=/etc/claude-gitconfig`, `GIT_CONFIG_SYSTEM=/dev/null` | check 16 |
| Chrome browser-extension RPC channel disabled | shadow injects `--no-chrome` and strips user `--chrome` so Claude Code never writes its `NativeMessagingHosts` manifest | check 03 (regression manifests as browser dirs under `~/.config`) |

Network egress (`--share-net`, NOT unshared) is deliberately open so
Claude can reach `api.anthropic.com`. No PASS/FAIL check — any
regression makes Claude fail on first use rather than silently.
Implicit: `--die-with-parent` (the sandbox disappears the moment
Claude does).

**Refusal-on-failure**: if the host can't run unprivileged user
namespaces, the installer refuses with a specific actionable
diagnostic. Silent degradation to "Claude installed but not
sandboxed" is itself a UX failure mode that gets people pwned.

## Threat model

**Defending against:** a developer running Claude Code inside a
devcontainer against an LLM-driven attack — a hostile prompt, a
hostile file Claude reads, or a hostile tool result — attempting to
exfiltrate host credentials, drive the host IDE, or escalate
privileges.

### In scope

The TL;DR table above. Each row corresponds to one observed
exfiltration path (env vars, dotfiles, IPC sockets, X11, TIOCSTI,
sudo, …) and the primitive that closes it.

### Out of scope

| Exposure | Why | Mitigation expected from you |
|---|---|---|
| **Workspace contents** | Claude has to read your workspace to do its job | Keep secrets outside the workspace (e.g. `~/.config/` mounted via your devcontainer's `mounts`). Don't put `.env` files with production credentials at the workspace root and expect them to be invisible |
| **Container host kernel** | A bwrap-aware kernel exploit is out of scope; this is a credential-isolation tool, not a sandbox against arbitrary native code | Keep your kernel patched; treat the devcontainer host as the trust boundary |
| **Network egress filtering** | Claude needs network. The sandbox shares the netns and does not run a per-process firewall | Run the devcontainer itself behind an egress filter if you need one |
| **Non-standard credential paths** | The installer scans `mount` and warns about `/kubeconfig`-style binds at install time, but cannot enumerate every custom mount | Audit your devcontainer's `mounts` block |
| **Non-root devcontainers; rootful Docker w/ default AppArmor** | v1 targets rootless podman + Debian/Ubuntu + `remoteUser=root` | Tracked for v2 |

## Deliberately exposed

Anything not in the lockdown list above is reachable from inside
Claude. The deliberate exposures:

| Path | Mode | Why |
|---|---|---|
| Workspace | rw | The whole point of Claude — see [workspace visibility caveat](#workspace-visibility-caveat) below. Resolution: `CLAUDE_SANDBOX_WORKSPACE_ROOT` if set; else `/workspaces` when `$PWD` is under it (so sibling devcontainer projects are writable); else `$PWD` |
| `/etc/claude-gitconfig` | r | Curated gitconfig: gh/glab credential helpers for `https://github.com` and `https://gitlab.diamond.ac.uk`, ssh→https `insteadOf` rewrites, regenerated at every shadow launch from your host's current `user.name`/`user.email` |
| `/etc/gitconfig` | r | Host's system gitconfig is reachable read-only but neutralised for `git` because `GIT_CONFIG_SYSTEM=/dev/null` — see [gitconfig defence-in-depth](#gitconfig-defence-in-depth) |
| `/root/.claude/` | rw | Claude's state, settings, skills, hooks. `install.sh` symlinks this to `/user-terminal-config/.claude` so the tree persists across rebuilds and is shared with every other devcontainer that mounts the same `terminal-config` dir |
| `/root/.claude.json` | rw | Account-level state (OAuth token, recent-projects list, settings). Symlinked alongside `~/.claude/`; without it the strict-under-/root tmpfs would swallow the token and re-prompt login every launch |
| `/root/.cache/` | rw | Tool caches Claude needs across runs (if present) |
| `/root/.config/gh/` | rw | `gh` CLI's token store. Required so `gh auth status` works and the curated gitconfig's `gh auth git-credential` helper can authenticate `git push` to GitHub without an OAuth popup |
| `/root/.config/glab-cli/` | rw | `glab` CLI's token store. Same reason as `gh`. Sibling paths under `/root/.config/` (VS Code state, other cred helpers, etc.) are NOT bound |
| `/root/.local/share/uv/` + single files `/root/.local/bin/{uv,uvx}` | rw | uv-managed Python interpreters + tool binaries. Without these binds, a project's `.venv/bin/python` symlink (pointing into `~/.local/share/uv/python/...`) resolves to nothing. See [uv bind discipline](#uv-bind-discipline) |
| `/usr/libexec/claude-sandbox/claude` | r | The real Claude binary, relocated here by the installer from `~/.local/bin/claude` so plain `claude` on the user's PATH always resolves to the shadow. The shadow exec's this same file via `bwrap`; a bind back to `~/.local/bin/claude` inside the sandbox keeps Claude Code's `installMethod=native` self-check happy |
| Network (`--share-net`) | — | Claude needs `api.anthropic.com` + GitHub/GitLab. See [network-identity disclosure](#network-identity-disclosure) |

### Workspace visibility caveat

Workspace contents are visible to Claude — this is irreducible.
Claude has to read your workspace to do its job. The sandbox
protects you against host-credential leaks via env vars, dotfiles,
and IPC sockets, but **anything you check out into your workspace is
by design reachable from Claude's tools**.

Practical rule: keep secrets outside the workspace (e.g., in
`~/.config/` mounted via your devcontainer's `mounts`). Don't put
`.env` files with production credentials at the workspace root and
expect them to be invisible.

<details>
<summary id="uv-bind-discipline">uv bind discipline</summary>

The whole `~/.local/bin/` directory is NOT bound — Claude Code
writes there via tmpfs at runtime and we want those writes
ephemeral. Only `uv` and `uvx` individually. `$HOME/.local/bin` is
appended to PATH so `uv` resolves without a full path; **appended,
not prepended**, so a malicious binary in `~/.local/bin/<sysname>`
cannot hijack a standard command.

</details>

<details>
<summary id="gitconfig-defence-in-depth">gitconfig defence-in-depth</summary>

Tools that scrub `GIT_*` env vars before spawning git (e.g.
pre-commit's `no_git_env`) will see the host `/etc/gitconfig`, which
is the intended behaviour — the defence-in-depth bind-mask we
previously layered here broke those tools without adding meaningful
protection beyond the env redirect. The host's `/root/.gitconfig` is
invisible via strict-under-/root, so there is no comparable concern
at $HOME.

</details>

<details>
<summary id="network-identity-disclosure">Network-identity disclosure</summary>

Because the network namespace is shared with the host, Claude can
enumerate the host's interface addresses, routing table, and DNS
resolver via `AF_NETLINK` / standard tooling (`ip addr`, `ip
route`, `/etc/resolv.conf`). This is network-identity disclosure,
not credential exfil — but it means the sandbox is visible to
internal services on the same host network. Don't run a local
metadata-style credential service on the loopback or RFC1918 of a
host that also runs `claude` unless you're OK with Claude reaching
it. `/verify-sandbox` flags this as an `[INCONCLUSIVE]` adversarial
probe so it stays on the radar.

</details>

<details>
<summary id="procfs-view">Procfs view: host PIDs are visible (accepted info-disclosure)</summary>

`--unshare-pid` reliably gives kernel-level pidns isolation (the
sandbox cannot `kill()` or `ptrace()` host or devcontainer processes
— check 07 verifies this via `/proc/self/status:NSpid:`). The
companion property — `/proc` reflecting *only* the sandbox's own
process tree — depends on bwrap successfully mounting procfs against
the new pidns, which fails on rootless nested-userns hosts (the
standard VS Code devcontainer pattern).

`claude-sandbox` always emits `--ro-bind /proc /proc` rather than
probing. Host PIDs are enumerable from inside the sandbox. This is
**information disclosure** (Claude can see the user's process tree
and command lines), **not credential exfil**. The credential-bearing
procfs entries — `/proc/<pid>/environ`, `/maps`, `/fd`, `/mem`,
`/cwd` — are gated by `PTRACE_MODE_READ_FSCREDS`, which under YAMA
`ptrace_scope=1` (the Ubuntu/Debian default and what every
devcontainer base image ships) is restricted to the caller's
descendants. The sandbox has no descendant relationship with VS
Code, terminal sessions, or other devcontainer processes, so those
reads `EACCES`. Check 07 still passes — kernel pidns isolation is
intact.

</details>

## Verifying

```
/verify-sandbox        # inside Claude
```

Runs the 16 PASS/FAIL checks against the live process and prints a
summary table. Any FAIL exits the command non-zero (so you can use
it as a CI assertion), and the FAIL line names which defence
regressed. The full spec lives at
`.claude/commands/verify-sandbox.md`.

A pre-prompt hook (`.claude/hooks/sandbox-check.sh`) runs a
sub-second subset of these checks before every prompt and blocks the
prompt if the sandbox is not intact.

## What's installed

Container-scoped — re-established by re-running `./install`,
typically wired into `postCreate.sh`:

| Path | Source | Purpose |
|---|---|---|
| `/usr/libexec/claude-sandbox/claude` | Anthropic installer (`curl -fsSL https://claude.ai/install.sh \| bash`), relocated | The real Claude binary, kept off the user's PATH so the shadow always wins |
| `/usr/local/bin/claude` | `.devcontainer/claude-sandbox/claude-shadow` (verbatim) | Shadow that wraps the real binary in `bwrap`. Falls through to the real binary when `IS_SANDBOX=1` so internal `claude` invocations from a hook don't recurse |
| `/etc/claude-gitconfig` | Generated | Curated gitconfig — regenerated from `git config --get user.{name,email}` on every shadow launch |

Workspace — placed-once, idempotent, never silently overwritten:

| Path | Behaviour |
|---|---|
| `<workspace>/.claude/settings.json` | Created from scratch if missing; one-key surgical merge of `hooks.UserPromptSubmit` only if pre-existing. No other settings key is ever touched. JSONC files (containing `//` comments) are refused with a paste-this snippet rather than parsed |
| `<workspace>/.claude/hooks/sandbox-check.sh` | The `UserPromptSubmit` hook that gatekeeps every prompt |

Not placed: `CLAUDE.md` and `README-CLAUDE.md` live in the meta-repo
for dogfooding.

## Running it

```
claude
```

The shadow on `$PATH` always wraps; you cannot accidentally run the
unwrapped binary from your normal shell. The curated gitconfig is
regenerated from the host's current `user.name`/`user.email` on
every launch, so a host gitconfig edit takes effect on the next
`claude` invocation with nothing to re-run.

## Authenticating

```
just gh-auth                    # github.com
just glab-auth                  # gitlab.com
just glab-auth gitlab.diamond.ac.uk
```

Both walk you through a fine-grained-PAT prompt without leaking the
token into shell history.
