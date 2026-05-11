#!/usr/bin/env bash
# Bash unit test for bwrap_argv_build. The argv is a pure function of
# (workspace, real_claude, "$@", $HOME, $CLAUDE_SANDBOX_GITCONFIG_PATH);
# this file checks string-equality for the security-critical tokens
# across three scenarios. Failures print a diff-friendly diagnostic.
#
# Run via `bash tests/bwrap_argv.sh`. The pytest wrapper
# `tests/test_bwrap_argv_bash.py` subprocesses this script and asserts
# return code 0 so the bash tests run as part of `uv run pytest`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARGV_SH="$REPO_ROOT/src/claude_sandbox/data/bwrap_argv.sh"

if [ ! -f "$ARGV_SH" ]; then
    echo "FAIL: cannot find $ARGV_SH" >&2
    exit 1
fi

# shellcheck source=../src/claude_sandbox/data/bwrap_argv.sh
source "$ARGV_SH"

PASSED=0
FAILED=0

assert_contains() {
    # assert_contains <name> <argv> <token>
    local name="$1" argv="$2" token="$3"
    if printf '%s\n' "$argv" | grep -qxF -- "$token"; then
        PASSED=$((PASSED+1))
    else
        FAILED=$((FAILED+1))
        echo "FAIL: $name — missing token: $token" >&2
        echo "----- argv -----" >&2
        printf '%s\n' "$argv" >&2
        echo "----------------" >&2
    fi
}

assert_not_contains() {
    local name="$1" argv="$2" token="$3"
    if printf '%s\n' "$argv" | grep -qxF -- "$token"; then
        FAILED=$((FAILED+1))
        echo "FAIL: $name — unexpected token: $token" >&2
    else
        PASSED=$((PASSED+1))
    fi
}

# --- Scenario 1: vanilla (workspace=/workspaces/foo, $HOME=/root, no .cache) ---

set +e
ARGV1="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build /workspaces/foo /test/.runtime/claude)"
set -e

assert_contains scenario1 "$ARGV1" "bwrap"
assert_contains scenario1 "$ARGV1" "--ro-bind"
assert_contains scenario1 "$ARGV1" "--cap-drop"
assert_contains scenario1 "$ARGV1" "ALL"
assert_contains scenario1 "$ARGV1" "--unshare-user-try"
assert_contains scenario1 "$ARGV1" "--unshare-pid"
assert_contains scenario1 "$ARGV1" "--unshare-ipc"
assert_contains scenario1 "$ARGV1" "--unshare-uts"
assert_contains scenario1 "$ARGV1" "--new-session"
assert_contains scenario1 "$ARGV1" "--die-with-parent"
assert_contains scenario1 "$ARGV1" "--clearenv"
# The /run/secrets mask is now conditional on the host having
# /run/secrets — bwrap can't mkdir into a read-only /run when the
# parent has no such subdir. We check both branches behaviourally.
if [ -d /run/secrets ]; then
    assert_contains scenario1 "$ARGV1" "/run/secrets"
else
    assert_not_contains scenario1 "$ARGV1" "/run/secrets"
fi
assert_contains scenario1 "$ARGV1" "IS_SANDBOX"
assert_contains scenario1 "$ARGV1" "/etc/claude-gitconfig"
# Gitconfigs are deliberately not bind-masked: the env redirect to
# /etc/claude-gitconfig + strict-under-/root handle the threat, and a
# bind to /dev/null over /etc/gitconfig broke pre-commit's sanitised
# subprocesses on EL9 (SELinux made the bound /dev/null read as EACCES).
assert_not_contains scenario1 "$ARGV1" "/root/.gitconfig"
assert_not_contains scenario1 "$ARGV1" "/etc/gitconfig"
assert_contains scenario1 "$ARGV1" "/test/.runtime/claude"
# Default mode mounts a fresh procfs; the bind-/proc fallback is off.
assert_contains scenario1 "$ARGV1" "--proc"

# --- Scenario 2: workspace at an unusual path ---

set +e
ARGV2="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build /srv/weird-workspace-path /test/.runtime/claude)"
set -e

# Workspace bind only fires if the directory exists; we pass a non-
# existent path so the binding is omitted, but the rest of the argv is
# unchanged. This is the scenario where the user runs `claude` from a
# transient directory.
assert_contains scenario2 "$ARGV2" "bwrap"
assert_contains scenario2 "$ARGV2" "--clearenv"
assert_not_contains scenario2 "$ARGV2" "/srv/weird-workspace-path"

# --- Scenario 3: $HOME/.cache present vs absent ---

TMPHOME="$(mktemp -d)"
trap 'rm -rf "$TMPHOME"' EXIT
mkdir -p "$TMPHOME/.claude"

set +e
ARGV3a="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.runtime/claude)"
set -e
assert_contains scenario3a "$ARGV3a" "$TMPHOME/.claude"
assert_not_contains scenario3a "$ARGV3a" "$TMPHOME/.cache"
# Neither credential dir exists in this fresh tmphome; both binds
# must be absent so a missing host directory cannot cause a launch
# failure.
assert_not_contains scenario3a "$ARGV3a" "$TMPHOME/.config/gh"
assert_not_contains scenario3a "$ARGV3a" "$TMPHOME/.config/glab-cli"
# .claude.json missing -> bind must be absent (same reasoning).
assert_not_contains scenario3a "$ARGV3a" "$TMPHOME/.claude.json"

mkdir -p "$TMPHOME/.cache"
set +e
ARGV3b="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.runtime/claude)"
set -e
assert_contains scenario3b "$ARGV3b" "$TMPHOME/.claude"
assert_contains scenario3b "$ARGV3b" "$TMPHOME/.cache"

# --- Scenario 3c: gh and glab credential dirs both present ---
mkdir -p "$TMPHOME/.config/gh" "$TMPHOME/.config/glab-cli"
set +e
ARGV3c="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.runtime/claude)"
set -e
assert_contains scenario3c "$ARGV3c" "$TMPHOME/.config/gh"
assert_contains scenario3c "$ARGV3c" "$TMPHOME/.config/glab-cli"
# A sibling under .config (e.g. VS Code) must NOT be bound even when
# the directory exists — only the explicit allowlist is exposed.
mkdir -p "$TMPHOME/.config/Code"
set +e
ARGV3d="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.runtime/claude)"
set -e
assert_not_contains scenario3d "$ARGV3d" "$TMPHOME/.config/Code"

# --- Scenario 3e: ~/.claude.json present is bound back ---
# Without this bind the strict-under-/root tmpfs would swallow Claude
# Code's OAuth token on every launch.
touch "$TMPHOME/.claude.json"
set +e
ARGV3e="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.runtime/claude)"
set -e
assert_contains scenario3e "$ARGV3e" "$TMPHOME/.claude.json"

# --- Scenario 3f: uv-managed pythons and `uv`/`uvx` binaries bound back ---
# Without ~/.local/share/uv the project's .venv/bin/python symlink
# resolves to nothing inside the sandbox. The uv/uvx binaries live
# at ~/.local/bin and are bound individually (not the whole bin/
# dir — Claude Code also writes into ~/.local/bin via tmpfs and we
# don't want those writes to leak back to the host).
mkdir -p "$TMPHOME/.local/share/uv" "$TMPHOME/.local/bin"
touch "$TMPHOME/.local/bin/uv" "$TMPHOME/.local/bin/uvx"
set +e
ARGV3f="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.runtime/claude)"
set -e
assert_contains scenario3f "$ARGV3f" "$TMPHOME/.local/share/uv"
assert_contains scenario3f "$ARGV3f" "$TMPHOME/.local/bin/uv"
assert_contains scenario3f "$ARGV3f" "$TMPHOME/.local/bin/uvx"
# PATH must include $HOME/.local/bin so `uv` resolves without a full path.
assert_contains scenario3f "$ARGV3f" \
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$TMPHOME/.local/bin"

# --- Scenario 3g: ~/.claude is a symlink to the shared terminal-config tree ---
# The installer points ~/.claude at /user-terminal-config/.claude when
# the devcontainer mounts the shared dir. `[ -d ]` follows the symlink
# and `--bind` resolves source paths on the host fs, so the bind argv
# token is still ~/.claude — the symlink target is invisible at the
# argv layer (bwrap dereferences it at mount time).
TMPSHARED="$(mktemp -d)"
trap 'rm -rf "$TMPHOME" "$TMPSHARED"' EXIT
mkdir -p "$TMPSHARED/.claude-shared"
rm -rf "$TMPHOME/.claude"
ln -s "$TMPSHARED/.claude-shared" "$TMPHOME/.claude"
set +e
ARGV3g="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.runtime/claude)"
set -e
# The bind still emits ~/.claude as both source and destination —
# bwrap follows the symlink at mount time, mounting the shared dir
# writably at the destination inside the sandbox.
assert_contains scenario3g "$ARGV3g" "$TMPHOME/.claude"

# --- Scenario 4: CLAUDE_SANDBOX_FRESH_PROC=0 swaps --proc for --ro-bind /proc ---
# Triggered by the shadow's launch-time probe when seccomp blocks
# mount(proc) (typical of nested podman/docker on RHEL). The fallback
# loses pid-namespace isolation in the procfs view but keeps the rest
# of the sandbox functional.

set +e
ARGV4="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    CLAUDE_SANDBOX_FRESH_PROC=0 \
    bwrap_argv_build /workspaces/foo /test/.runtime/claude)"
set -e
# In degraded mode the fresh-proc mount is gone and the bind-/proc pair
# is present. grep -A1 matches the line *after* the flag so we assert
# the pair as a unit, not as two unrelated tokens.
if printf '%s\n' "$ARGV4" | grep -A1 '^--ro-bind$' | grep -qx '/proc'; then
    PASSED=$((PASSED+1))
else
    FAILED=$((FAILED+1))
    echo "FAIL: scenario4 — expected --ro-bind /proc /proc fallback pair" >&2
fi
assert_not_contains scenario4 "$ARGV4" "--proc"

# --- Scenario 5: real_claude present → bind appears at ~/.local/bin/claude ---
# Without this bind the strict-under-/root tmpfs hides ~/.local/bin/claude,
# and Claude Code's installMethod=native self-check warns "claude command
# not found at /root/.local/bin/claude" on every launch.
TMPREALCLAUDE="$(mktemp)"
trap 'rm -rf "$TMPHOME" "$TMPSHARED"; rm -f "$TMPREALCLAUDE"' EXIT
chmod 0755 "$TMPREALCLAUDE"
set +e
ARGV5="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build /workspaces/foo "$TMPREALCLAUDE")"
set -e
assert_contains scenario5 "$ARGV5" "$TMPREALCLAUDE"
assert_contains scenario5 "$ARGV5" "/root/.local/bin/claude"

# Scenario 5b: real_claude missing → no bind line for ~/.local/bin/claude.
# The shadow refuses to launch in this case anyway; this just makes sure
# the argv builder doesn't emit a broken --bind that would abort bwrap.
set +e
ARGV5b="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build /workspaces/foo /nonexistent/path/to/claude)"
set -e
assert_not_contains scenario5b "$ARGV5b" "/root/.local/bin/claude"

echo "bwrap_argv.sh: $PASSED passed / $FAILED failed"
[ "$FAILED" -eq 0 ]
