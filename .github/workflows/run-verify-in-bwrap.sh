#!/usr/bin/env bash
# CI helper: render the same bwrap argv the installer would render,
# then run `claude-sandbox verify` inside it. The trick that makes this
# work without the real Claude binary: bind `bash` at
# `/opt/claude/bin/claude` so when bwrap execs `/opt/claude/bin/claude`
# the inner process is bash but `/proc/1/comm` reads "claude" — which
# is what /verify-sandbox check 02 wants.
#
# Why we don't use uv inside the bwrap: uv is typically under $HOME,
# which the strict-under-/root inversion wipes. We rely on `uv sync`
# having already produced $REPO/.venv/bin/claude-sandbox (the typer
# entry point) and call that directly. The venv is reachable because
# the bwrap argv binds $REPO RW.

set -euo pipefail

REPO="$GITHUB_WORKSPACE"
ARGV_SH="$REPO/src/claude_sandbox/data/bwrap_argv.sh"

# Drop a fake-claude that bwrap will exec. The bind-mount inside the
# sandbox makes /proc/1/comm read "claude" once bash execs into PID 1.
sudo mkdir -p /opt/claude/bin
sudo cp "$(which bash)" /opt/claude/bin/claude
sudo chmod 0755 /opt/claude/bin/claude

# bwrap_argv only binds $HOME/.claude and $HOME/.cache back after the
# --tmpfs $HOME wipe — make sure both exist so uv's project cache
# (under .cache) and the verifier's spec lookup don't fail.
mkdir -p "$HOME/.claude" "$HOME/.cache"

# shellcheck source=../../src/claude_sandbox/data/bwrap_argv.sh
source "$ARGV_SH"

# The inner exec runs the verifier from the synced project venv.
# bash -lc is needed because /opt/claude/bin/claude is bash; the -lc
# string is what bash actually executes.
# The leading ls -lA dump exists so a Check 03 failure on CI surfaces
# what's actually under $HOME inside the sandbox — the spec only
# accepts .claude and .cache, and silent failures are hard to debug.
INNER="echo '--- HOME path:' \"\$HOME\"; echo '--- /tmp:'; ls -lA /tmp 2>&1; echo '--- HOME:'; ls -lA \"\$HOME\" 2>&1; echo '--- env HOME:'; env | grep '^HOME=' 2>&1; echo '--- end debug ---'; exec '$REPO/.venv/bin/claude-sandbox' verify --workspace '$REPO'"

# Build the argv. Workspace bind = the repo (so the verifier finds
# .claude/commands/verify-sandbox.md and the venv).
mapfile -t ARGV < <(bwrap_argv_build "$REPO" /opt/claude/bin/claude -lc "$INNER")

# Echo for debug.
printf '  argv> %s\n' "${ARGV[@]}"

# Exec inside bwrap. The verifier returns 0 on all-PASS.
exec "${ARGV[@]}"
