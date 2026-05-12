#!/usr/bin/env bash
# Install smoke test. Runs the installer with INSTALL_PREFIX +
# INSTALL_WORKSPACE pointed at fresh tmpdirs and asserts on the
# resulting file placement. Set CLAUDE_SANDBOX_SMOKE=1 to skip
# apt-install and the curl-install of the real Claude binary.
#
#   CLAUDE_SANDBOX_SMOKE=1 bash tests/smoke.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PASSED=0
FAILED=0

pass() { PASSED=$((PASSED+1)); }
fail() {
    FAILED=$((FAILED+1))
    echo "FAIL: $1" >&2
}

PREFIX="$(mktemp -d)"
WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$PREFIX" "$WORKSPACE"' EXIT

export CLAUDE_SANDBOX_SMOKE=1
export INSTALL_PREFIX="$PREFIX"
export INSTALL_WORKSPACE="$WORKSPACE"

run_install() {
    bash "$REPO_ROOT/.devcontainer/claude-sandbox/install.sh" >/dev/null 2>&1
}

# First install.
if ! run_install; then
    fail "first install run exited non-zero"
fi

# Shadow placement.
SHADOW_DEST="$PREFIX/usr/local/bin/claude"
if [ -f "$SHADOW_DEST" ]; then
    pass
else
    fail "shadow not placed at $SHADOW_DEST"
fi

if [ -x "$SHADOW_DEST" ]; then
    pass
else
    fail "shadow not executable"
fi

# install(1) -m 0755 — check mode.
if [ "$(stat -c '%a' "$SHADOW_DEST" 2>/dev/null)" = "755" ]; then
    pass
else
    fail "shadow mode is $(stat -c '%a' "$SHADOW_DEST" 2>/dev/null), expected 755"
fi

# Shebang.
if head -1 "$SHADOW_DEST" | grep -qxF '#!/usr/bin/env bash'; then
    pass
else
    fail "shadow does not start with #!/usr/bin/env bash"
fi

# Workspace hook placement.
HOOK_DEST="$WORKSPACE/.claude/hooks/sandbox-check.sh"
if [ -f "$HOOK_DEST" ]; then
    pass
else
    fail "workspace hook not placed at $HOOK_DEST"
fi
if [ -x "$HOOK_DEST" ]; then
    pass
else
    fail "workspace hook not executable"
fi

# Settings.json placement + content.
SETTINGS="$WORKSPACE/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
    pass
else
    fail "settings.json not placed at $SETTINGS"
fi

if jq -e . "$SETTINGS" >/dev/null 2>&1; then
    pass
else
    fail "settings.json does not parse as JSON"
fi

if jq -r '.hooks.UserPromptSubmit[0].hooks[0].command' "$SETTINGS" \
        2>/dev/null | grep -qx '.claude/hooks/sandbox-check.sh'; then
    pass
else
    fail "settings.json missing UserPromptSubmit sandbox-check.sh entry"
fi

# Idempotency: second install must be byte-for-byte stable.
SHADOW_SUM_A="$(sha256sum "$SHADOW_DEST" | awk '{print $1}')"
HOOK_SUM_A="$(sha256sum "$HOOK_DEST" | awk '{print $1}')"
SETTINGS_SUM_A="$(sha256sum "$SETTINGS" | awk '{print $1}')"

if ! run_install; then
    fail "second install run exited non-zero"
fi

SHADOW_SUM_B="$(sha256sum "$SHADOW_DEST" | awk '{print $1}')"
HOOK_SUM_B="$(sha256sum "$HOOK_DEST" | awk '{print $1}')"
SETTINGS_SUM_B="$(sha256sum "$SETTINGS" | awk '{print $1}')"

if [ "$SHADOW_SUM_A" = "$SHADOW_SUM_B" ]; then
    pass
else
    fail "shadow drifted across install re-run"
fi
if [ "$HOOK_SUM_A" = "$HOOK_SUM_B" ]; then
    pass
else
    fail "workspace hook drifted across install re-run"
fi
if [ "$SETTINGS_SUM_A" = "$SETTINGS_SUM_B" ]; then
    pass
else
    fail "settings.json drifted across install re-run"
fi

# Settings merge with pre-existing JSON: write a settings.json with
# unrelated keys, re-run, assert merge preserves them and dedups our hook.
MERGE_WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$PREFIX" "$WORKSPACE" "$MERGE_WORKSPACE"' EXIT
mkdir -p "$MERGE_WORKSPACE/.claude"
cat > "$MERGE_WORKSPACE/.claude/settings.json" <<'JSON'
{
  "permissions": {"allow": ["Bash(ls:*)"]},
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "some-other-hook.sh"}]}
    ]
  }
}
JSON

INSTALL_WORKSPACE="$MERGE_WORKSPACE" \
    bash "$REPO_ROOT/.devcontainer/claude-sandbox/install.sh" >/dev/null 2>&1

if jq -e '.permissions.allow[0] == "Bash(ls:*)"' \
        "$MERGE_WORKSPACE/.claude/settings.json" >/dev/null 2>&1; then
    pass
else
    fail "merge dropped pre-existing permissions"
fi
if jq -e 'any(.hooks.UserPromptSubmit[].hooks[]; .command == ".claude/hooks/sandbox-check.sh")' \
        "$MERGE_WORKSPACE/.claude/settings.json" >/dev/null 2>&1; then
    pass
else
    fail "merge did not add our sandbox-check.sh hook"
fi
if jq -e 'any(.hooks.UserPromptSubmit[].hooks[]; .command == "some-other-hook.sh")' \
        "$MERGE_WORKSPACE/.claude/settings.json" >/dev/null 2>&1; then
    pass
else
    fail "merge dropped pre-existing hook"
fi

# Re-merge dedup: running again must NOT duplicate our entry.
INSTALL_WORKSPACE="$MERGE_WORKSPACE" \
    bash "$REPO_ROOT/.devcontainer/claude-sandbox/install.sh" >/dev/null 2>&1
OUR_HOOK_COUNT="$(jq '[.hooks.UserPromptSubmit[].hooks[] | select(.command == ".claude/hooks/sandbox-check.sh")] | length' \
    "$MERGE_WORKSPACE/.claude/settings.json")"
if [ "$OUR_HOOK_COUNT" = "1" ]; then
    pass
else
    fail "duplicate sandbox-check.sh entries after re-merge (count=$OUR_HOOK_COUNT)"
fi

# Bwrap sanity check (when bwrap is available AND we are not already
# inside a sandbox — nested userns is forbidden). CI installs bwrap as
# a pre-step; locally we tolerate absence or nesting as a skip.
if command -v bwrap >/dev/null 2>&1 && [ "${IS_SANDBOX:-}" != "1" ]; then
    if bwrap --ro-bind / / -- /bin/true >/dev/null 2>&1; then
        pass
    else
        fail "bwrap --ro-bind / / -- /bin/true failed (runner cannot enter a sandbox)"
    fi
fi

echo "smoke.sh: $PASSED passed / $FAILED failed"
[ "$FAILED" -eq 0 ]
