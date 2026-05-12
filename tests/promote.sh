#!/usr/bin/env bash
# Promote smoke test. Drives `.devcontainer/claude-sandbox/promote.sh`
# against tmpdir "host workspaces" and asserts:
#   - the curated trees (commands, skills, hooks) and statusline land
#     byte-for-byte at $TARGET/.claude/...
#   - settings.json gains our hook + statusLine without trampling
#     pre-existing keys
#   - re-runs are byte-stable (idempotency)
#   - refuses on TARGET == REPO_ROOT
#
#   bash tests/promote.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROMOTE="$REPO_ROOT/.devcontainer/claude-sandbox/promote.sh"

PASSED=0
FAILED=0
pass() { PASSED=$((PASSED+1)); }
fail() {
    FAILED=$((FAILED+1))
    echo "FAIL: $1" >&2
}

TARGET="$(mktemp -d)"
trap 'rm -rf "$TARGET"' EXIT

run_promote() {
    bash "$PROMOTE" "$@" >/dev/null 2>&1
}

# First promote into a clean target.
if ! run_promote "$TARGET"; then
    fail "first promote run exited non-zero"
fi

# Curated tree placement: pick representative files known to exist in
# this repo's .claude/. The set is intentionally small — full coverage
# would couple this test to skill churn.
expect_file() {
    local rel="$1"
    if [ -f "$TARGET/$rel" ]; then
        pass
    else
        fail "missing $rel in promoted target"
    fi
}
expect_file ".claude/commands/verify-sandbox.md"
expect_file ".claude/commands/memo.md"
expect_file ".claude/skills/claude-sandbox/SKILL.md"
expect_file ".claude/skills/diagnose/SKILL.md"
expect_file ".claude/hooks/sandbox-check.sh"
expect_file ".claude/statusline-command.sh"
expect_file ".claude/settings.json"

# Byte-equal: a promoted skill must match the source exactly.
if cmp -s "$REPO_ROOT/.claude/skills/claude-sandbox/SKILL.md" \
          "$TARGET/.claude/skills/claude-sandbox/SKILL.md"; then
    pass
else
    fail "promoted claude-sandbox/SKILL.md differs from source"
fi

# Hook + statusLine wired into settings.json.
SETTINGS="$TARGET/.claude/settings.json"
if jq -e . "$SETTINGS" >/dev/null 2>&1; then
    pass
else
    fail "promoted settings.json does not parse as JSON"
fi
if jq -e 'any(.hooks.UserPromptSubmit[].hooks[]; .command == ".claude/hooks/sandbox-check.sh")' \
        "$SETTINGS" >/dev/null 2>&1; then
    pass
else
    fail "promoted settings.json missing sandbox-check.sh hook"
fi
if jq -e '.statusLine.command == ".claude/statusline-command.sh"' \
        "$SETTINGS" >/dev/null 2>&1; then
    pass
else
    fail "promoted settings.json missing .statusLine"
fi

# Idempotency: re-run must be byte-stable across the whole tree.
SUMS_A="$(cd "$TARGET" && find .claude -type f -print0 | sort -z | xargs -0 sha256sum)"
if ! run_promote "$TARGET"; then
    fail "second promote run exited non-zero"
fi
SUMS_B="$(cd "$TARGET" && find .claude -type f -print0 | sort -z | xargs -0 sha256sum)"
if [ "$SUMS_A" = "$SUMS_B" ]; then
    pass
else
    fail "promote re-run drifted target tree"
fi

# Merge: pre-existing settings.json keys survive, our entries are
# added, re-running doesn't duplicate.
MERGE_TARGET="$(mktemp -d)"
trap 'rm -rf "$TARGET" "$MERGE_TARGET"' EXIT
mkdir -p "$MERGE_TARGET/.claude"
cat > "$MERGE_TARGET/.claude/settings.json" <<'JSON'
{
  "permissions": {"allow": ["Bash(ls:*)"]},
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "their-hook.sh"}]}
    ]
  }
}
JSON
run_promote "$MERGE_TARGET" || fail "promote into pre-existing settings exited non-zero"
if jq -e '.permissions.allow[0] == "Bash(ls:*)"' \
        "$MERGE_TARGET/.claude/settings.json" >/dev/null 2>&1; then
    pass
else
    fail "merge dropped pre-existing permissions"
fi
if jq -e 'any(.hooks.UserPromptSubmit[].hooks[]; .command == "their-hook.sh")' \
        "$MERGE_TARGET/.claude/settings.json" >/dev/null 2>&1; then
    pass
else
    fail "merge dropped pre-existing UserPromptSubmit hook"
fi
if jq -e 'any(.hooks.UserPromptSubmit[].hooks[]; .command == ".claude/hooks/sandbox-check.sh")' \
        "$MERGE_TARGET/.claude/settings.json" >/dev/null 2>&1; then
    pass
else
    fail "merge did not add our sandbox-check.sh hook"
fi

run_promote "$MERGE_TARGET" || fail "promote re-run on merged target exited non-zero"
OUR_HOOK_COUNT="$(jq '[.hooks.UserPromptSubmit[].hooks[] | select(.command == ".claude/hooks/sandbox-check.sh")] | length' \
    "$MERGE_TARGET/.claude/settings.json")"
if [ "$OUR_HOOK_COUNT" = "1" ]; then
    pass
else
    fail "duplicate sandbox-check.sh entries after re-merge (count=$OUR_HOOK_COUNT)"
fi

# Refuse on TARGET == REPO_ROOT: promoting onto the sandbox itself
# would clobber the source of truth, so this must exit non-zero AND
# leave the repo untouched.
if bash "$PROMOTE" "$REPO_ROOT" >/dev/null 2>&1; then
    fail "promote did not refuse TARGET == REPO_ROOT"
else
    pass
fi

# Refuse on non-existent target dir.
NONEXISTENT="$(mktemp -d)/does-not-exist"
if bash "$PROMOTE" "$NONEXISTENT" >/dev/null 2>&1; then
    fail "promote did not refuse a missing target dir"
else
    pass
fi

echo "promote.sh: $PASSED passed / $FAILED failed"
[ "$FAILED" -eq 0 ]
