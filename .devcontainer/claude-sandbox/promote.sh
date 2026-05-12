#!/usr/bin/env bash
# claude-sandbox promote: seed the sandbox's curated `.claude/`
# (commands, skills, hooks, statusline, sandbox-check hook) into a
# target host workspace so a Claude session run there gets the same
# toolkit. Idempotent: re-runs are byte-stable via install_file's
# `cmp -s` short-circuit and wire_settings_*'s dedup.
#
# Does NOT touch `~/.claude` — that channel is reserved for
# cross-container shared state (OAuth, memories). Issue #18 spells out
# the rationale.
#
# Usage:
#   bash .devcontainer/claude-sandbox/promote.sh [TARGET]
#   just promote [TARGET]                              # preferred
#
# TARGET defaults to $PWD. The script refuses when TARGET resolves to
# the sandbox clone itself — promoting onto yourself is a no-op the
# user almost certainly didn't mean.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# REPO_ROOT is the clone — two levels above .devcontainer/claude-sandbox.
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

TARGET_INPUT="${1:-$PWD}"
if [ ! -d "$TARGET_INPUT" ]; then
    echo "claude-sandbox: refusing — target '$TARGET_INPUT' is not a directory." >&2
    exit 1
fi
TARGET="$(cd "$TARGET_INPUT" && pwd)"

if [ "$TARGET" = "$REPO_ROOT" ]; then
    echo "claude-sandbox: refusing — target is the sandbox repo itself; nothing to promote." >&2
    exit 1
fi

# Hand WORKSPACE off to install.sh's wire_settings_{hook,statusline} —
# they operate on "$WORKSPACE/.claude/settings.json".
INSTALL_WORKSPACE="$TARGET"
export INSTALL_WORKSPACE
# shellcheck source=./install.sh
. "$SCRIPT_DIR/install.sh"

# copy_tree: install_file every regular file under $1 to the same
# relative path under $2. install_file's `cmp -s` short-circuit makes
# re-runs no-ops; mode 0755 matches what install.sh already uses for
# the hook and statusline (skill .md files end up 0755 too — harmless,
# users can `chmod 0644` if they care).
copy_tree() {
    local src_dir="$1" dst_dir="$2"
    [ -d "$src_dir" ] || return 0
    local src rel
    while IFS= read -r -d '' src; do
        rel="${src#"$src_dir/"}"
        install_file "$src" "$dst_dir/$rel"
    done < <(find "$src_dir" -type f -print0)
}

copy_tree "$REPO_ROOT/.claude/commands" "$TARGET/.claude/commands"
copy_tree "$REPO_ROOT/.claude/skills"   "$TARGET/.claude/skills"
copy_tree "$REPO_ROOT/.claude/hooks"    "$TARGET/.claude/hooks"
install_file "$REPO_ROOT/.claude/statusline-command.sh" \
             "$TARGET/.claude/statusline-command.sh"
wire_settings_hook
wire_settings_statusline

echo "claude-sandbox: promote complete."
echo "  source:   $REPO_ROOT/.claude/"
echo "  target:   $TARGET/.claude/"
