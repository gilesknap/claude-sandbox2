---
name: claude-sandbox
description: Invariants and historical reversals to preserve when modifying this repo's bwrap-based Claude sandbox. Covers the "real claude stays off PATH" invariant, the credential-scoping policy (PATs never cross-container), the three Ubuntu-24.04 GitHub-runner workarounds for bwrap, and the two deliberately-walked-back paths (Python orchestration, embedding inside python-copier-template). Use when editing `.devcontainer/claude-sandbox/claude-shadow`, `.devcontainer/claude-sandbox/install.sh`, `install`, `tests/`, `.github/workflows/ci.yml`, or `.claude/commands/verify-sandbox.md`; or when a suggestion would re-introduce Python tooling (pyproject, uv, pytest, a CLI), move the sandbox back into python-copier-template, or add persistent gh/glab PAT storage. Surface this skill before such suggestions land.
---

# claude-sandbox

Project-specific invariants. The code already documents *what* it does;
this skill documents *why we did it that way* and *what regressions
to refuse*. Threat model lives in `README-CLAUDE.md`; live verification
is `/verify-sandbox` (spec at `.claude/commands/verify-sandbox.md`).

## Invariant 1 — plain `claude` MUST resolve to the shadow

The Anthropic installer (`curl -fsSL https://claude.ai/install.sh | bash`)
does two things in one shot:

1. Drops the real binary at `~/.local/bin/claude`.
2. Prepends `$HOME/.local/bin` to the user's shell rc
   (`.bashrc` / `.zshrc`).

After the next shell starts, `which claude` resolves to the real
binary and the bwrap shadow at `/usr/local/bin/claude` is bypassed —
**sandbox escape via plain `claude`**.

The installer's `install_claude_binary` relocates the real binary to
`/usr/libexec/claude-sandbox/claude` (off the user's PATH) and the
shadow binds it back to `~/.local/bin/claude` *inside* the sandbox so
Claude's `installMethod=native` self-check still sees the conventional
path.

**Refuse these as regressions:**

- Any "simplification" that skips the relocate-after-curl step and
  leaves the real binary at `~/.local/bin/claude` on the host.
- Removing the unconditional bind-back of `~/.local/bin/claude` inside
  the sandbox (it's not gated on the host file existing — the dest
  is created on the in-sandbox tmpfs `$HOME`).
- Tests `tests/bwrap_argv.sh` scenarios 1 & 4a guard the bind pair.
  If you change the bind, update both scenarios.

**Acceptable swap:** if Anthropic's installer ever grows
`--no-modify-path`, you can drop the relocate and use that flag
instead — as long as plain `claude` still cannot resolve past
`/usr/local/bin/claude`.

## Invariant 2 — PATs are container-scoped; `just gh-auth` per rebuild is deliberate

The re-paste-on-rebuild ceremony for `gh` / `glab` PATs is a deliberate
tradeoff, not a defect. Do not propose putting tokens in
`~/.config/terminal-config/`, a `devcontainer-shared-cache` volume, or
anywhere else mounted across multiple devcontainers.

**Why:** gh fine-grained PATs typically cover multiple repos. A PAT in
any path mounted into every devcontainer would let any Claude session
on the host reach every repo the PAT covers — the blast radius is
"compromise any one session, lose access to all repos the PAT touches."
The user has weighed the rebuild ceremony against this and chosen the
ceremony.

This is *different* from `~/.claude` and `~/.claude.json`, which are
deliberately cross-container via the `link_terminal_config` symlink in
`install.sh` (one Claude login, persistent settings/skills/oauth).
PATs are repo-scoped credentials and stay container-scoped. Don't
conflate the two.

**Refuse as regressions:**

- New persistent-credential mounts (volume, bind, anywhere) for `gh`
  or `glab` PATs.
- Re-purposing the (currently deleted) `/cache` Docker volume for
  tokens. Restoring `/cache` for *caches* is fine; for tokens, not.

If a future request says "stop re-pasting the PAT" — surface this
tradeoff explicitly rather than implementing the shortcut.

## Invariant 3 — bwrap on Ubuntu 24.04 GitHub runners needs three workarounds

`ubuntu-latest` runners ship configured in ways that break bwrap. The
`ci.yml` workflow has the workarounds already; this section exists so
future edits don't strip them as "looks unnecessary".

The three failure modes, in the order they cascade if you don't fix
them:

1. **`setting up uid map: Permission denied`** —
   `kernel.apparmor_restrict_unprivileged_userns=1` is the runner
   default. Fix: relax the sysctl and install an unconfined AppArmor
   profile for `/usr/bin/bwrap`.
2. **`/run/secrets` doesn't exist** — the sandbox does
   `--tmpfs /run/secrets`, which needs the mountpoint to exist.
   Fix: `sudo mkdir -p /run/secrets` before invoking bwrap.
3. **`$GITHUB_WORKSPACE` lives under `$HOME=/home/runner`** — any
   path-positional check that asserts "$HOME contains only X" gets
   tripped by the workspace bind. Fix: `export HOME=/tmp/sandbox-home`
   before the bwrap step (and `mkdir -p "$HOME/.claude" "$HOME/.cache"`).

All three steps are required, in order. The current `.github/workflows/ci.yml`
applies them; see the `Allow unprivileged userns`, `Pre-create
/run/secrets`, and per-step `HOME=/tmp/sandbox-home` blocks. Five
push-and-iterate cycles landed this — don't re-discover it.

## Design principle — keep dogfood ≈ guest

This repo's own devcontainer (the "dogfood" case in
`.devcontainer/devcontainer.json`) and consumer devcontainers
(`git clone` + `sudo ./install` inside someone else's container)
should go through the same setup path. When a fix could live either
in `devcontainer.json`/`postCreate.sh`/`initializeCommand.sh` or in
`install.sh`, prefer `install.sh` so guest devcontainers get it for
free. Sample: per-file binds for `/root/.claude{,.json}` were dropped
once `link_terminal_config` covered both paths uniformly — only the
shared `/user-terminal-config` bind remains in `devcontainer.json`.

**Why:** a code path that only fires for the dogfood container is one
fewer chance for the consumer flow to silently diverge, and the
sandbox's audit surface stays single-track.

**Refuse as regressions:** dogfood-only `postCreate` steps,
`initializeCommand` work, or `devcontainer.json` mounts that could
have been done in `install.sh` instead. Ask "would this work for a
clone+install inside an unrelated devcontainer?" — if the answer is
"only with extra steps", push it into `install.sh`.

## Historical reversals — raise before re-treading

Two paths have been tried and deliberately walked back. If a future
change suggests either, surface the history first and re-justify
against the underlying principle (**the sandbox's surface must stay
small enough to audit in one read**) before proceeding.

### Reversal 1 — Python orchestration was ripped out

The repo went `embedded bash → standalone bash → Python package +
typer CLI → bash-only` (commits `25e67ce` slice-1, `a35b8ee` slice-2,
then `bf65407` "feat: bash-only rewrite — drop Python package,
self-contained shadow", May 12 2026, issue #14 / PR #15).

**Why it came back to bash:** the tool is fundamentally one bash
function building a bwrap argv. A ~110 KB Python package — pyproject,
uv lockfile, pytest scaffolding, 37 unit tests, a typer CLI — was
overwhelming for that surface and made the security-critical bits
harder to audit (you had to read through entrypoints, dispatch, and
argv assembly across multiple modules to convince yourself the bwrap
flags were right). The bash-only rewrite collapses the whole thing
to ~80 lines of shadow + ~80 lines of installer.

**Refuse without justification:**

- "Let's add a small Python CLI for nicer error messages / config /
  arg parsing."
- "Let's bring back pytest / uv / a `src/` package — it's only a
  little code."
- Anything that re-introduces `pyproject.toml`, `uv.lock`,
  `src/claude_sandbox/`, or `test_*.py` to this repo.

The `CLAUDE.md` at the repo root already says "Bash-only. No Python
package, no uv, no pytest — don't add them back." This skill explains
the why.

### Reversal 2 — extracted from python-copier-template

The sandbox originally lived embedded in `python-copier-template` as
`.devcontainer/claude-sandbox.sh` (a single bash script using
`unshare -m` + tmpfs overlays). It was extracted into its own repo
because:

- A security tool needs **one canonical, audit-friendly home**, not
  a templated copy in every project the generator produces.
- The bwrap-based model replaces the older `unshare -m` approach; the
  defences (`--cap-drop ALL`, `--clearenv` allow-list, strict-under-
  `/root` inversion, `NO_NEW_PRIVS`, etc.) are bwrap-native and would
  be awkward to reproduce inside a per-project template.
- A standalone repo can have a versioned release surface, a CI of its
  own, and `/verify-sandbox` as a first-class command.

`/workspaces/python-copier-template/.devcontainer/claude-sandbox.sh`
still exists as historical prior art, but it is **not** the
maintained surface. Don't port changes there or suggest "let's move
this back into the template so projects get it for free."

**Refuse without justification:**

- Adding a `template/` directory or `copier.yml` to this repo.
- "Let's also keep a copy synced into python-copier-template" — the
  template should *consume* this repo (via `just claude` running the
  shadow, devcontainer feature, etc.), not embed it.

## Diagnostic discipline — silent in-sandbox check failures

When a check inside the sandbox fails silently (a subprocess swallows
stdout/stderr), inject a debug `INNER` step that runs the same check
body verbatim and prints its output *before* exec'ing the real
verifier. The original Check 03 silent failure was unsolvable until
we printed `extras` directly — turned out `--bind-try /dev/null` masks
themselves create entries under `$HOME` (the spec hadn't whitelisted
them). One `printf` of the captured variable beats hours of guessing
from the outside.

## Where things live

| Concern                       | File                                                |
|-------------------------------|-----------------------------------------------------|
| bwrap argv construction       | `.devcontainer/claude-sandbox/claude-shadow`        |
| Installer (relocate + wire)   | `.devcontainer/claude-sandbox/install.sh`           |
| Root-shim installer entry     | `install`                                           |
| bwrap argv unit tests         | `tests/bwrap_argv.sh`                               |
| End-to-end install smoke test | `tests/smoke.sh`                                    |
| CI workflow                   | `.github/workflows/ci.yml`                          |
| Live verification spec        | `.claude/commands/verify-sandbox.md`                |
| Pre-prompt gate hook          | `.claude/hooks/sandbox-check.sh`                    |
| Threat model + binds rationale| `README-CLAUDE.md`                                  |
| Recipes (test, gh-auth, …)    | `justfile`                                          |

If you're touching any of these, re-read this skill first.
