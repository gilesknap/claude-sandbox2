#!/usr/bin/env bash
# Bash unit test for the bwrap_argv_build function defined inline in
# .devcontainer/claude-sandbox/claude-shadow. The shadow exposes a
# CLAUDE_SHADOW_SOURCE_ONLY=1 guard so we can source the function
# definitions without running the launch body.
#
# We assert on the argv as a contract with bwrap (line-equality grep),
# not on the internal control flow that built it. Failures print a
# diff-friendly diagnostic.
#
# Run via `bash tests/bwrap_argv.sh`.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SHADOW="$REPO_ROOT/.devcontainer/claude-sandbox/claude-shadow"

if [ ! -f "$SHADOW" ]; then
    echo "FAIL: cannot find $SHADOW" >&2
    exit 1
fi

# Pull bwrap_argv_build into scope without running the shadow's launch
# body. The shadow returns early when CLAUDE_SHADOW_SOURCE_ONLY=1.
export CLAUDE_SHADOW_SOURCE_ONLY=1
# shellcheck source=../.devcontainer/claude-sandbox/claude-shadow
source "$SHADOW"

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

assert_pair() {
    # assert_pair <name> <argv> <flag> <value>  — flag on one line, value
    # on the next. Catches paired --ro-bind /proc /proc style emissions.
    local name="$1" argv="$2" flag="$3" value="$4"
    if printf '%s\n' "$argv" | grep -A1 "^${flag}\$" | grep -qxF -- "$value"; then
        PASSED=$((PASSED+1))
    else
        FAILED=$((FAILED+1))
        echo "FAIL: $name — expected pair $flag → $value" >&2
    fi
}

# --- Scenario 1: vanilla (workspace=/workspaces/foo, $HOME=/root) ---
unset TERM LANG LC_ALL LC_CTYPE LC_MESSAGES LC_TIME LC_COLLATE LC_NUMERIC LC_MONETARY
ARGV1="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build /workspaces/foo /test/.local/bin/claude)"

assert_contains scenario1 "$ARGV1" "bwrap"
assert_contains scenario1 "$ARGV1" "--ro-bind"
assert_contains scenario1 "$ARGV1" "--dev"
assert_contains scenario1 "$ARGV1" "/dev"
# Unconditional --ro-bind /proc /proc.
assert_pair scenario1 "$ARGV1" "--ro-bind" "/proc"
assert_contains scenario1 "$ARGV1" "--cap-drop"
assert_contains scenario1 "$ARGV1" "ALL"
# All five unshare flags including --unshare-user-try.
assert_contains scenario1 "$ARGV1" "--unshare-user-try"
assert_contains scenario1 "$ARGV1" "--unshare-pid"
assert_contains scenario1 "$ARGV1" "--unshare-ipc"
assert_contains scenario1 "$ARGV1" "--unshare-uts"
assert_contains scenario1 "$ARGV1" "--unshare-cgroup-try"
assert_contains scenario1 "$ARGV1" "--die-with-parent"
# --new-session is DROPPED (delegated to script(1) wrap).
assert_not_contains scenario1 "$ARGV1" "--new-session"
# The shadow's procfs probe is gone — no --proc primitive emission.
assert_not_contains scenario1 "$ARGV1" "--proc"
# Env scrub.
assert_contains scenario1 "$ARGV1" "--clearenv"
assert_contains scenario1 "$ARGV1" "PATH"
assert_contains scenario1 "$ARGV1" "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin"
assert_contains scenario1 "$ARGV1" "HOME"
assert_contains scenario1 "$ARGV1" "/root"
assert_contains scenario1 "$ARGV1" "USER"
assert_contains scenario1 "$ARGV1" "root"
assert_contains scenario1 "$ARGV1" "IS_SANDBOX"
assert_contains scenario1 "$ARGV1" "GIT_CONFIG_GLOBAL"
assert_contains scenario1 "$ARGV1" "/etc/claude-gitconfig"
assert_contains scenario1 "$ARGV1" "GIT_CONFIG_SYSTEM"
assert_contains scenario1 "$ARGV1" "/dev/null"
# Final terminator and real claude path.
assert_contains scenario1 "$ARGV1" "--"
assert_contains scenario1 "$ARGV1" "/test/.local/bin/claude"
# /run/secrets mask only when the host has it.
if [ -d /run/secrets ]; then
    assert_contains scenario1 "$ARGV1" "/run/secrets"
else
    assert_not_contains scenario1 "$ARGV1" "/run/secrets"
fi

# --- Scenario 2: workspace empty string → no workspace bind line ---
ARGV2="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "" /test/.local/bin/claude)"
# No bind for an empty workspace. The argv is otherwise intact.
assert_contains scenario2 "$ARGV2" "bwrap"
assert_contains scenario2 "$ARGV2" "--clearenv"
# Nothing that looks like a path bind for /tmp/... or /workspaces/... should appear.
# We can't enumerate all possible non-emissions, but workspace=""
# means the workspace bind branch is skipped.

# --- Scenario 3: workspace at an unusual non-existent path ---
ARGV3="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build /srv/weird-workspace-path /test/.local/bin/claude)"
assert_not_contains scenario3 "$ARGV3" "/srv/weird-workspace-path"

# --- Scenario 4: bind-back loop over $HOME (tmpdir-based fixture) ---
TMPHOME="$(mktemp -d)"
trap 'rm -rf "$TMPHOME"' EXIT
mkdir -p "$TMPHOME/.claude" "$TMPHOME/.config/gh"

ARGV4a="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.local/bin/claude)"
assert_contains scenario4a "$ARGV4a" "$TMPHOME/.claude"
assert_contains scenario4a "$ARGV4a" "$TMPHOME/.config/gh"
# Absent paths must NOT appear.
assert_not_contains scenario4a "$ARGV4a" "$TMPHOME/.cache"
assert_not_contains scenario4a "$ARGV4a" "$TMPHOME/.config/glab-cli"
assert_not_contains scenario4a "$ARGV4a" "$TMPHOME/.local/share/uv"
assert_not_contains scenario4a "$ARGV4a" "$TMPHOME/.claude.json"
assert_not_contains scenario4a "$ARGV4a" "$TMPHOME/.local/bin/uv"
assert_not_contains scenario4a "$ARGV4a" "$TMPHOME/.local/bin/uvx"
assert_not_contains scenario4a "$ARGV4a" "$TMPHOME/.local/bin/claude"

# Now populate the full set and re-check.
mkdir -p "$TMPHOME/.cache" "$TMPHOME/.config/glab-cli" "$TMPHOME/.local/share/uv" "$TMPHOME/.local/bin"
touch "$TMPHOME/.claude.json" "$TMPHOME/.local/bin/uv" "$TMPHOME/.local/bin/uvx" "$TMPHOME/.local/bin/claude"
# A sibling under .config (e.g. VS Code) must NOT be bound even when
# present — only the explicit allowlist is exposed.
mkdir -p "$TMPHOME/.config/Code"

ARGV4b="$(HOME="$TMPHOME" CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    bwrap_argv_build "$TMPHOME" /test/.local/bin/claude)"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.claude"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.cache"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.config/gh"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.config/glab-cli"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.local/share/uv"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.claude.json"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.local/bin/uv"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.local/bin/uvx"
assert_contains scenario4b "$ARGV4b" "$TMPHOME/.local/bin/claude"
assert_not_contains scenario4b "$ARGV4b" "$TMPHOME/.config/Code"
# Workspace ($TMPHOME exists) IS bound.
# (--bind <src> <dst> emits the path twice; existence is enough.)

# --- Scenario 5: pass-through env (TERM, LANG) appear as --setenv pairs ---
ARGV5="$(HOME=/root CLAUDE_SANDBOX_GITCONFIG_PATH=/etc/claude-gitconfig \
    TERM=xterm-256color LANG=en_US.UTF-8 \
    bwrap_argv_build /workspaces/foo /test/.local/bin/claude)"
assert_contains scenario5 "$ARGV5" "TERM"
assert_contains scenario5 "$ARGV5" "xterm-256color"
assert_contains scenario5 "$ARGV5" "LANG"
assert_contains scenario5 "$ARGV5" "en_US.UTF-8"

# --- Scenario 6: defence-in-depth masks at $HOME ---
assert_contains scenario6 "$ARGV1" "/root/.netrc"
assert_contains scenario6 "$ARGV1" "/root/.Xauthority"
assert_contains scenario6 "$ARGV1" "/root/.ICEauthority"

echo "bwrap_argv.sh: $PASSED passed / $FAILED failed"
[ "$FAILED" -eq 0 ]
