# PRD: claude-sandbox — bwrap-isolated Claude Code for rootless-podman + Debian/Ubuntu devcontainers, Python-CLI driven

## Problem Statement

A developer working inside a devcontainer wants to run Claude Code with strong, automatic isolation from their host credentials, IDE bridges, and shell environment — but they don't want to commit to a particular base image, copier template, or devcontainer feature to get there.

They want:

- a one-line install that works inside any Debian/Ubuntu rootless-podman devcontainer they happen to be attached to,
- a sandbox materially harder than `--cap-add=SYS_ADMIN`-based prior art (strict-under-`/root` by inversion, `--clearenv` with explicit re-export, `/run/secrets` masked, `--new-session` to block terminal injection),
- a clear escape hatch when they decide to keep the install (`git add` what they want, `git ignore` what they don't — no special "graduate" command, no machine-modified Dockerfile),
- an opt-in path to grab curated Claude skills/commands from the project's own `.claude/` toolkit, without anything being silently dropped into their workspace,
- and a project they can read in one sitting — not 3,300 lines of bash with embedded `jq`/`awk` programs.

The previous attempt at this (`gilesknap/claude-sandbox`, archived) shipped the bash version. It worked. It also acquired a SettingsMerger, JustfileMerger, GitignoreManager, GraduationRecipe, and a sentinel-bracketed managed-block infrastructure — all of which existed to support "land curated artifacts in your workspace, gitignored, with a graduation flow to commit later." That UX cost about half the codebase. This restart cuts the merging dance entirely: artifacts go where the user explicitly asks for them, and `git add` is the graduation.

## Solution

A standalone repo published at `gilesknap/claude-sandbox` (currently `claude-sandbox2` during the proving period). The user runs one command inside an attached devcontainer:

```
curl -fsSL https://raw.githubusercontent.com/gilesknap/claude-sandbox/main/install | bash
```

The `install` bash script is a thin probe-and-bootstrap shim:

1. Probe the host (apt available? unprivileged userns working? mount-scan warnings?).
2. `apt install -y bubblewrap nodejs gh ca-certificates curl jq`.
3. Curl-install `glab`, Claude Code, and `uv` (if absent).
4. `git clone` this repo into `/opt/claude-sandbox-src`.
5. `cd /opt/claude-sandbox-src && uv sync` (creates a project-local venv).
6. `exec uv run claude-sandbox install "$@"`.

The Python CLI (`claude-sandbox`, typer-based) does everything past that point: places the shadow `claude` binary on `$PATH`, generates the curated `/etc/claude-gitconfig`, writes the workspace's `.claude/settings.json` (with the runtime sandbox-check hook wired), and copies `.claude/hooks/sandbox-check.sh` into the workspace. All idempotent — re-running `install` after a devcontainer rebuild re-establishes the container-scoped pieces without disturbing workspace edits.

The sandbox model is the threat-model of the prior project, carried verbatim:

- **Strict-under-`/root` by inversion** — `--ro-bind / /` for the system, `--tmpfs /root` to wipe home, then `--bind /root/.claude` and `--bind /root/.cache` to expose only what Claude legitimately needs. Closes every `$HOME`-based credential tool past, present, and future without enumeration.
- **`--clearenv`** with explicit re-export of only `PATH`, `HOME`, `USER`, `TERM`, `LANG`, `LC_*`, `IS_SANDBOX`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`. Closes the "user has `GH_TOKEN` exported in their shell" leak.
- **`/run/secrets` masked** with tmpfs (Docker/Compose secrets path).
- **`--new-session`** to block ioctl(TIOCSTI) terminal injection.
- **File masks** over `/root/.gitconfig`, `/etc/gitconfig`, `/root/.netrc`, `/root/.Xauthority`, `/root/.ICEauthority` as defence in depth.
- **`--cap-drop ALL`**, `--unshare-pid`, `--unshare-ipc`, `--unshare-uts`, `--share-net`, `--die-with-parent`. `NO_NEW_PRIVS` is implicit (bwrap sets it before `exec`).

The user verifies at any time by running `/verify-sandbox` from inside Claude (slash command, markdown-spec) or `claude-sandbox verify` from a shell. Both extract the same 18 check bodies from `.claude/commands/verify-sandbox.md` and assert PASS/FAIL against the live process.

The installer **refuses to install** on an unsupported host (non-Debian, userns blocked, etc.) with a specific actionable diagnostic. Silent degradation to "Claude installed but not sandboxed" is a UX failure mode that gets people pwned.

The user can opt into shipped skills and commands at any time:

```
claude-sandbox install-skill diagnose tdd grill-with-docs
claude-sandbox install-command grill-me memo verify-sandbox
claude-sandbox install-skill --bundle pocock
```

These default to writing under `<workspace>/.claude/`, refusing on existing-with-different-content (override with `--force`). The user decides whether to commit (`git add .claude/`) or ignore (one-line `.gitignore` snippet from the README). No `graduate` command exists because none is needed.

## User Stories

1. As a developer attached to a Debian/Ubuntu rootless-podman devcontainer, I want to install sandboxed Claude Code with one curl-bash command, so that I can start using it without modifying `devcontainer.json` or `Dockerfile` upfront.
2. As a developer worried about an LLM-driven attack on my host, I want Claude to be unable to read any file under `$HOME` other than its own state, so that no credential tool I forgot to enumerate can leak data.
3. As a developer with `GH_TOKEN` exported in my shell, I want that token to be invisible inside the Claude sandbox, so that a hostile prompt cannot exfiltrate it.
4. As a developer running Docker Compose with `secrets:` blocks, I want `/run/secrets` masked inside the sandbox.
5. As a developer using VS Code's "Open Folder in Container" workflow, I want the VS Code IPC bridges in `/tmp` to be invisible inside the sandbox, so that a hostile prompt cannot drive my host editor.
6. As a developer with X11 forwarding configured, I want the Claude sandbox unable to reach my host X server.
7. As a developer who runs `claude` from habit (without any wrapper), I want the shadow on `$PATH` to wrap it in `bwrap` automatically, so that I cannot accidentally bypass the sandbox.
8. As a developer who has invoked Claude inside the sandbox, I want internal `claude` invocations from hooks or tools to skip the wrap (rather than recursively re-wrap), so that the sandbox doesn't break Claude's own machinery.
9. As a developer auditing the sandbox, I want a `/verify-sandbox` slash command (from inside Claude) and a `claude-sandbox verify` CLI (from a shell) that both run the same 18 checks against the live process, so that I can confirm the sandbox is intact at any point.
10. As a developer hitting an unsupported host (rootful Docker with default AppArmor, hardened sysctls, non-Debian distro), I want the installer to refuse with a specific actionable diagnostic, so that I am never left with an installed-but-non-functional sandbox.
11. As a developer running Claude through the sandbox after the host's gitconfig has been updated, I want the curated `/etc/claude-gitconfig` to be regenerated with my current `user.name` / `user.email`, so that commits Claude makes are correctly attributed.
12. As a developer rebuilding my devcontainer, I want the install to be re-establishable with one shell command (a tiny `bootstrap.sh` shim I commit to `.devcontainer/`, configured as `postCreateCommand`), so that rebuilds are automatic.
13. As a developer who wants to grab one of the project's curated skills, I want `claude-sandbox install-skill diagnose` to copy it into my workspace `.claude/skills/`, refusing if it would clobber a different version of the same skill.
14. As a developer who wants a family of related skills, I want `claude-sandbox install-skill --bundle pocock` to read from a `bundles.toml` data file and install all named skills.
15. As a developer who installed skills in my workspace, I want `git add .claude/` to be the entire "make this permanent" flow, so that I don't have to learn a special graduation command.
16. As a developer who wants to keep the install temporary, I want one paste-able `.gitignore` snippet in the README, so that the workspace stays uncommitted.
17. As a developer browsing what skills/commands ship with the project, I want `claude-sandbox list-skills` and `claude-sandbox list-commands` to enumerate what's available in the cloned source.
18. As a developer who installed `toolbox` from this project, I want it to enumerate **both** my user-global `~/.claude/` toolkit and my workspace-local `.claude/` overrides, so that I see everything Claude actually has access to.
19. As a developer needing to push to GitHub from inside Claude, I want the curated `/etc/claude-gitconfig` to register `gh` as the credential helper for `https://github.com`, so that `git push` works without surfacing a VS Code OAuth popup.
20. As a developer using GitLab as well as GitHub, I want `glab` available inside the sandbox as a peer of `gh`, so that both forges are first-class.
21. As the project maintainer, I want all install-time logic in Python with a typer CLI and pytest test suite, so that I can iterate on the orchestrator without parsing nested `jq` / `awk` programs in shell.
22. As the project maintainer, I want the meta-repo's own `.claude/` to BE the canonical source of shipped skills/commands (no `share/workspace/` indirection, no symlinks), so that editing a skill once updates both the dogfooded meta-repo behaviour and what `install-skill` ships.

## Implementation Decisions

### Distribution

- **Curl-bash entry point** — `https://raw.githubusercontent.com/gilesknap/claude-sandbox/main/install` (canonical name reverts to `claude-sandbox` once the rewrite is proven; during the proving period the URL points at `claude-sandbox2`).
- **Source tree clone** — `git clone` into `/opt/claude-sandbox-src` (container-scoped). Provides the Python package, the bash hot-path artifacts, the `.claude/` payload, and the `bundles.toml`. Upgrade is `cd /opt/claude-sandbox-src && git pull && uv sync` — wrapped as `claude-sandbox upgrade`.
- **Re-running after a container rebuild** — the user commits a 5-line `bootstrap.sh` shim to `.devcontainer/` that just curl-bashes the install, and a one-line `postCreateCommand` to `devcontainer.json`. Both are documented snippets the user pastes — the install command does not mutate `devcontainer.json` or `Dockerfile`.

### Operating envelope

- **Target environment v1**: rootless podman + Debian/Ubuntu base images + `remoteUser=root`.
- **Refuse on non-Debian** with a "this distro isn't supported yet — file an issue" message.
- **Refuse on userns blocked** (rootful Docker + default AppArmor, `kernel.unprivileged_userns_clone=0`, etc.) with a specific diagnostic naming likely causes and fixes. The userns probe runs both at install time AND at every `claude` launch as a defence against runtime drift.
- **Mount-scan warnings** — at install time, scan `mount` output for non-standard host bind-mounts that look like host credentials (`/kubeconfig`, `/secrets`, etc.) and warn the user.
- **Nothing is installed in the failure path** — the sandbox is the value; a non-functional sandbox does not warrant a partial install.

### CLI surface (typer, 7 commands)

```
claude-sandbox install                       # idempotent; safe to re-run on rebuild
claude-sandbox verify                        # runs the 18 check bodies from outside Claude
claude-sandbox upgrade                       # cd /opt/claude-sandbox-src && git pull && uv sync && exec install
claude-sandbox list-skills                   # enumerate .claude/skills/ in the clone
claude-sandbox list-commands                 # enumerate .claude/commands/ in the clone
claude-sandbox install-skill NAME...   [--force] [--all] [--bundle NAME]
claude-sandbox install-command NAME... [--force] [--all] [--bundle NAME]
```

Defaults:

- `install-skill` / `install-command` write to `<workspace>/.claude/` (not user-global). No `--global` flag in v1 (YAGNI).
- Multi-arg, glob-supporting (`'pocock-*'`).
- Refuse if destination exists with different content; `--force` overwrites; `--all` installs everything.
- `--bundle NAME` reads from `src/claude_sandbox/data/bundles.toml`. Bundles ship empty in v1 (or with `pocock` if its source skills exist); the CLI plumbing ships in slice 2 regardless.

### What the install actually places

**Container-scoped** (regenerated by `install` on every run):
- `/usr/local/bin/claude` — shadow on `$PATH`, always wraps real Claude in `bwrap`. Falls through to the real binary if `IS_SANDBOX=1` (so internal Claude-spawns-Claude doesn't recurse).
- `/usr/local/bin/claude-sandbox` — the typer CLI's executable shim (re-exec into `uv run claude-sandbox` from `/opt/claude-sandbox-src`).
- `/opt/claude/bin/claude` — the real Claude binary, moved from `~/.local/bin/claude` so the shadow on `$PATH` always wins.
- `/etc/claude-gitconfig` — curated gitconfig, regenerated at every `install` (and at every `claude` launch via the shadow) from the host's current `git config user.name/email`. Registers `gh` as the credential helper for `https://github.com`.
- `/opt/claude-sandbox-src/.venv` — the Python project venv.

**Workspace-scoped** (place-once, idempotent):
- `<workspace>/.claude/settings.json` — created from scratch if missing; surgical merge of `hooks.UserPromptSubmit` only if pre-existing. No other key path is touched.
- `<workspace>/.claude/hooks/sandbox-check.sh` — copied if missing; refuses if present with different content.

**Not placed by `install`** (opt-in via separate commands or `git add`):
- Skills, commands — opt-in via `install-skill` / `install-command`.
- `CLAUDE.md`, `README-CLAUDE.md` — neither is placed in the user's workspace by `install`. They live in the meta-repo for dogfooding. If a user wants them, the README documents how to copy.
- `.gitignore` mutation — never. The user decides what to commit and what to ignore (README provides one-line snippets for both directions).
- `Dockerfile` / `devcontainer.json` — never. Bootstrap snippets for `postCreateCommand` are documented for the user to paste.

### The runtime sandbox-check hook

The shipped `sandbox-check.sh` is a `UserPromptSubmit` hook that fires on every prompt and refuses to proceed unless `IS_SANDBOX=1` is set in the environment. It is sandbox-essential — belt-and-suspenders against the "user invoked Claude via a non-shadow path" bypass scenario.

`install` auto-wires it into `<workspace>/.claude/settings.json`. Implementation: a ~40-line mini-merger that touches exactly one key path (`hooks.UserPromptSubmit`), append-with-dedupe, refuses on real disagreement (e.g., user has wired the same hook with different args). Never touches any other key. Never reads or writes `permissions`, `additionalDirectories`, `env`, etc.

### Sandbox model (bwrap)

Carried verbatim from the prior project's threat-model design (see Solution section above for the enumeration).

The bwrap argv lives in a single bash file (`src/claude_sandbox/data/bwrap_argv.sh`), sourced by the shadow `claude` on the launch hot path. **Bash, not Python.** Reasons:

- The shadow runs on every `claude` invocation; depending on uv at launch would tie Claude availability to a uv install staying healthy, and Python startup adds 30-80 ms to every wrap.
- The bwrap argv is the single piece of launch-path logic an auditor has to read; keeping it in one bash file with no Python dependency keeps the audit surface minimal.
- If uv ever breaks (a bad release, a network blip during install), the shadow `claude` continues to launch.

### What stays bash, what's Python

| File | Language | Why |
|---|---|---|
| `install` | bash | Curl-bashable; cannot assume any interpreter beyond bash itself. |
| `src/claude_sandbox/data/claude-shadow` | bash | Runs on every `claude` invocation; latency budget < 50 ms. |
| `src/claude_sandbox/data/bwrap_argv.sh` | bash | Sourced by the shadow on the launch hot path. |
| `.claude/hooks/sandbox-check.sh` | bash | `UserPromptSubmit` hook fires on every prompt; bash is sub-millisecond, Python is ~30 ms. |
| Everything else (`probe`, `installer`, `settings_merger`, `skill_installer`, `verifier`, `gitconfig`, `cli`) | Python | Install-time orchestration with no latency sensitivity. |

### Source tree layout

```
claude-sandbox/
├── README.md                                # project README
├── README-CLAUDE.md                         # threat model + verification docs
├── CLAUDE.md                                # for dogfooding
├── pyproject.toml                           # uv-managed
├── uv.lock
├── install                                  # bash, curl-bashable entry point
├── src/
│   └── claude_sandbox/
│       ├── __init__.py
│       ├── cli.py                           # typer app, all 7 subcommands
│       ├── probe.py                         # apt detect, userns probe, mount-scan warnings
│       ├── installer.py                     # orchestrator: file placement + venv wiring
│       ├── settings_merger.py               # one-key mini-merger (~40 LoC)
│       ├── skill_installer.py               # install-skill / install-command logic
│       ├── verifier.py                      # /verify-sandbox runner
│       ├── gitconfig.py                     # curated /etc/claude-gitconfig
│       └── data/
│           ├── claude-shadow                # bash, the wrap
│           ├── bwrap_argv.sh                # bash, sourced by shadow
│           └── bundles.toml                 # skill/command bundles
├── .claude/                                 # canonical, dogfooded directly (no share/ indirection)
│   ├── settings.json                        # for dogfooding (with our hook block live)
│   ├── hooks/
│   │   └── sandbox-check.sh                 # the hook script — also the source for install-time copy
│   ├── skills/
│   │   ├── diagnose/
│   │   ├── tdd/
│   │   ├── grill-with-docs/
│   │   ├── improve-codebase-architecture/
│   │   └── triage/
│   └── commands/
│       ├── grill-me.md
│       ├── memo.md
│       ├── verify-sandbox.md                # the spec; verifier.py extracts check bodies via re
│       ├── write-a-skill.md
│       ├── zoom-out.md
│       ├── to-prd.md
│       ├── to-issues.md
│       ├── toolbox.md
│       └── toolbox-update.md
└── tests/
    ├── conftest.py
    ├── fixtures/
    └── test_*.py                            # pytest, parametrised, fixture-driven
```

Key points:

- **`.claude/` is canonical at the repo root** — no `share/workspace/` indirection, no symlinks. The meta-repo IS the dogfood target. Editing a shipped skill updates both how Claude behaves on this repo AND what `install-skill` ships, in one move.
- **Bash artifacts under `src/claude_sandbox/data/`** — package data, read via `importlib.resources` at install time. Standard packaging idiom.
- **`src/` layout** — modern uv/hatchling default; catches accidental cwd-imports during dev.
- **No `Justfile`, no `lib/`, no `share/`, no `.devcontainer/`** — the CLI is `claude-sandbox` directly; bash data is package data; the meta-repo doesn't need its own devcontainer (host-mode work in v2 covers contributor onboarding).

### Shipped skills / commands

**Skills** (in `.claude/skills/`):
- `diagnose`
- `tdd`
- `grill-with-docs`
- `improve-codebase-architecture`
- `triage`

**Commands** (in `.claude/commands/`):
- `grill-me`
- `memo`
- `write-a-skill`
- `zoom-out`
- `to-prd`
- `to-issues`
- `toolbox` (enumerates **both** `~/.claude/` and `<workspace>/.claude/`)
- `toolbox-update`
- `verify-sandbox` (the markdown spec consumed by both the slash command and `claude-sandbox verify`)

**Dropped vs prior project:** `setup-matt-pocock-skills` (a meta-installer skill) — replaced by the `--bundle` CLI mechanism + `bundles.toml` data file. Removes a special case.

### Modules (Python)

- **`probe`** — environment introspection (apt detection, bwrap+userns probe, mount-scan). Pure read; returns structured ok/fail with diagnostics. Refusal-paths raise typed exceptions (`UnsupportedHostError`, `UserNamespacesBlockedError`).
- **`installer`** — orchestrator. Probes → places container-scoped artifacts (shadow, real-claude move, gitconfig) → places workspace-scoped artifacts (settings hook merge, hook script).
- **`settings_merger`** — JSON in → JSON out. One key path (`hooks.UserPromptSubmit`), append-with-dedupe, raises `SettingsConflictError` on real disagreement.
- **`skill_installer`** — copies skills/commands from `<src>/.claude/{skills,commands}/` to `<workspace>/.claude/{skills,commands}/`. Refuses on existing-with-different-content; `--force` overrides; `--bundle` reads from `bundles.toml`.
- **`verifier`** — extracts the 18 check bodies from `.claude/commands/verify-sandbox.md` via `re`, runs them, prints PASS/FAIL/Summary. Used by `claude-sandbox verify`.
- **`gitconfig`** — reads host `user.name`/`user.email`, generates `/etc/claude-gitconfig` with the `gh` credential helper for `https://github.com`. Atomic write.
- **`cli`** — the `claude-sandbox` typer entry point. Maps subcommands to module functions, converts typed exceptions to exit codes + stderr.

## Testing Decisions

Good tests for this codebase exercise the public function of each module — input fixtures in, output fixtures out — and avoid coupling to internal helpers.

**pytest, fixture-driven:**

- **`test_settings_merger.py`** — clean install, surgical merge into existing settings, conflict refusal (assert exception type AND stderr message), idempotent re-merge byte-equality, never-touch-non-target-keys invariant.
- **`test_skill_installer.py`** — copy-if-missing, refuse-if-different-content, `--force` overwrites, `--all` installs everything, bundle resolution via `bundles.toml`, glob expansion.
- **`test_verifier.py`** — markdown-fence extraction is a pure function: given a spec string and a check number N, returns the bash body. Tested with fixture markdown specs.
- **`test_gitconfig.py`** — output content check given mocked `git config` outputs, atomic-write check (no partial file ever exists on disk).
- **`test_probe.py`** — given fixture `mount` output, asserts the warning lines emitted match a fixture. Kernel-userns probe is not unit-tested (single bash invocation).
- **`test_installer.py`** — orchestrator dry-run mode that records what would be placed where, asserted against fixtures.

**bash unit tests (kept minimal):**

- **`bwrap_argv`** — argv string-equality check against fixtures. The argv builder is a pure function of `($workspace, $claude_path)` → argv. Run via `bash tests/bwrap_argv.sh`. Tests cover: vanilla call, workspace at unusual path, `/root/.cache` present vs absent.
- The shadow `claude` and the `sandbox-check.sh` hook are exercised via the integration test (below); their per-line behaviour is too small to deserve unit coverage.

**CI integration test** (folded into slice 2):

- Build a fresh Debian/Ubuntu image, `bash install` against it, then `claude-sandbox verify`, assert all 18 checks PASS.
- Second variant: install on a host with userns deliberately disabled, assert refusal-with-documented-diagnostic.

## Slicing

**Issue #1**: this PRD.

**Issue #2 — Slice 1: tracer bullet** (mostly bash + minimal Python):
- `install` script (probe → apt + curl + uv → clone → uv sync → exec)
- Python package skeleton with just `claude-sandbox install` and `claude-sandbox verify` working end-to-end
- Shadow `claude` + `bwrap_argv.sh` (full sandbox model from day one — the threat model is the value, no degraded "tracer" version of it)
- `.claude/settings.json` + `.claude/hooks/sandbox-check.sh` placed in workspace
- `verify-sandbox` markdown spec with all 18 checks
- Acceptance: `bash install` on a fresh devcontainer → `claude-sandbox verify` returns all-PASS; `/verify-sandbox` from inside Claude returns all-PASS.

**Issue #3 — Slice 2: full Python CLI + shipped payload + CI:**
- Remaining CLI commands (`upgrade`, `list-skills`, `list-commands`, `install-skill`, `install-command`)
- `bundles.toml` machinery (file + CLI plumbing; bundles themselves can ship empty)
- `settings_merger.py` mini-merger logic (full coverage)
- All shipped skills/commands payload (the canonical `.claude/` content)
- pytest suite covering all modules
- CI workflow: build fresh devcontainer, run install, run verify, assert all-PASS
- README, README-CLAUDE.md, CLAUDE.md
- Acceptance: all CLI commands work; CI green on fresh-build; pytest green.

**Issue #4 (placeholder) — v2: non-root devcontainer + host-mode install** — both blocked on the same `$HOME`-vs-`/root` parameterisation work. Filed immediately as a punt; no work.

**Issue #5 (placeholder) — v2: macOS support** — separate sandbox primitive (`sandbox-exec`/Seatbelt). Different project, conceptually. Filed for visibility; no work.

## Out of Scope

- **Distros other than Debian/Ubuntu.** Adding more is mechanical (per-distro package manager wrapper) once v1 is stable.
- **Container runtimes other than rootless podman.** Docker Desktop, Codespaces likely work but not tested. Rootful Docker on Linux with default AppArmor breaks the userns probe and is explicitly out of scope.
- **Non-root devcontainers.** v1 assumes `remoteUser=root`. Same parameterisation as host-mode; deferred together (issue #4).
- **Host-mode install (outside devcontainers).** Deferred to v2 (issue #4).
- **Persistent named volumes for PAT caching.** Re-authenticating after a rebuild is acceptable friction (one `gh auth login` invocation).
- **Modifying `Dockerfile` or `devcontainer.json` automatically.** Snippets are documented for the user to paste. Auto-mutation is too brittle across hand-tuned multi-stage builds and existing `postCreateCommand` chains.
- **Dev Container Feature distribution.** Considered and rejected — too slow, and they don't compose with the no-contamination requirement.
- **An IDE extension (`anthropic.claude-code`) inside the sandbox.** The extension talks to the host VS Code via the same `/tmp` IPC sockets the sandbox masks; opening a bridge for it would defeat the sandbox. CLI-only.
- **Workspace `.claude/` gitignore management** (and the corresponding "graduate" command). The user decides what to commit; `git add` is the entire promotion flow. README provides paste-able snippets for both directions.
- **Workspace `settings.json` merging beyond one key.** The `settings_merger` only touches `hooks.UserPromptSubmit`. Any other settings the user has are entirely theirs.
- **Workspace `justfile` generation / merging.** The CLI is `claude-sandbox` directly. No `just` recipes shipped.
- **`README-CLAUDE.md` placed in the user's workspace.** It lives in the meta-repo as documentation. Users who want a copy run `cp /opt/claude-sandbox-src/README-CLAUDE.md ./` themselves.

## Further Notes

- **The simplification arc vs the prior bash project**: the new project drops SettingsMerger (full), JustfileMerger entirely, GitignoreManager entirely, GraduationRecipe entirely, and the sentinel-bracketed managed-block infrastructure. What replaces them is "the user types `git add` to commit, or pastes a one-line gitignore to ignore, and `install` only ever touches one key in one settings file." Reduces surface area by roughly half versus the prior project's ~3,300 lines.
- **The user-visible value-add remains the sandbox.** Failure to establish a working sandbox results in refusal-to-install, never a degraded "Claude installed but not sandboxed" state.
- **Workspace contents are visible to Claude** (irreducible — Claude has to read the workspace to do its job). The README documents this and recommends keeping secrets outside the workspace.
- **Why the meta-repo's `.claude/` is canonical** instead of a `share/workspace/` staging directory with symlinks: the symlink trick was needed in the prior project because `share/workspace/` was the source of truth that `bootstrap.sh` read from. With Python and `importlib.resources`, the source of truth can just be `.claude/` at the repo root — no indirection, no symlinks, no two-place edits.
- **Why typer**: it gives idiomatic typed CLI surface, automatic `--help`, automatic shell completion, and is the de-facto Python CLI library in 2026. The 7-command surface fits comfortably without `typer` adding surface area we don't need.
- **Why `uv` (not pip / pipx / poetry)**: fastest install, lockfile-first, project-scoped venv at `/opt/claude-sandbox-src/.venv` matches the existing container-scoped source-tree convention; one tool for the package + venv + script entry.
