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
    bwrap_argv_build /workspaces/foo /opt/claude/bin/claude)"
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
assert_contains scenario1 "$ARGV1" "/opt/claude/bin/claude"
# Default mode mounts a fresh procfs; the bind-/proc fallback is off.
assert_contains scenario1 "$ARGV1" "--proc"

# --- Scenario 2: workspace at an unusual path ---

set +e
ARGV2="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build /srv/weird-workspace-path /opt/claude/bin/claude)"
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
    bwrap_argv_build "$TMPHOME" /opt/claude/bin/claude)"
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
    bwrap_argv_build "$TMPHOME" /opt/claude/bin/claude)"
set -e
assert_contains scenario3b "$ARGV3b" "$TMPHOME/.claude"
assert_contains scenario3b "$ARGV3b" "$TMPHOME/.cache"

# --- Scenario 3c: gh and glab credential dirs both present ---
mkdir -p "$TMPHOME/.config/gh" "$TMPHOME/.config/glab-cli"
set +e
ARGV3c="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /opt/claude/bin/claude)"
set -e
assert_contains scenario3c "$ARGV3c" "$TMPHOME/.config/gh"
assert_contains scenario3c "$ARGV3c" "$TMPHOME/.config/glab-cli"
# A sibling under .config (e.g. VS Code) must NOT be bound even when
# the directory exists — only the explicit allowlist is exposed.
mkdir -p "$TMPHOME/.config/Code"
set +e
ARGV3d="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /opt/claude/bin/claude)"
set -e
assert_not_contains scenario3d "$ARGV3d" "$TMPHOME/.config/Code"

# --- Scenario 3e: ~/.claude.json present is bound back ---
# Without this bind the strict-under-/root tmpfs would swallow Claude
# Code's OAuth token on every launch.
touch "$TMPHOME/.claude.json"
set +e
ARGV3e="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /opt/claude/bin/claude)"
set -e
assert_contains scenario3e "$ARGV3e" "$TMPHOME/.claude.json"

# --- Scenario 4: CLAUDE_SANDBOX_FRESH_PROC=0 swaps --proc for --ro-bind /proc ---
# Triggered by the shadow's launch-time probe when seccomp blocks
# mount(proc) (typical of nested podman/docker on RHEL). The fallback
# loses pid-namespace isolation in the procfs view but keeps the rest
# of the sandbox functional.

set +e
ARGV4="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    CLAUDE_SANDBOX_FRESH_PROC=0 \
    bwrap_argv_build /workspaces/foo /opt/claude/bin/claude)"
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

echo "bwrap_argv.sh: $PASSED passed / $FAILED failed"
[ "$FAILED" -eq 0 ]
