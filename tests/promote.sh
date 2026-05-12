#!/usr/bin/env bash
# Promote smoke test. Drives `.devcontainer/claude-sandbox/promote.sh`
# against tmpdir "host workspaces" and asserts:
#   - curated .claude/{commands,skills,hooks,statusline}, settings merge
#   - install machinery: .devcontainer/claude-sandbox/* (no root shim)
#   - .devcontainer/postCreate.sh created (or appended) with the
#     install.sh invocation
#   - the devcontainer.json paste-this snippet is always printed
#   - devcontainer.json is NEVER modified (even when present)
#   - re-runs are byte-stable
#   - refusals on TARGET == REPO_ROOT and missing target
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

run_promote() {
    bash "$PROMOTE" "$@" >/dev/null 2>&1
}

# ============================================================
# Section 1: clean target.
# ============================================================
TARGET="$(mktemp -d)"
trap 'rm -rf "$TARGET"' EXIT

if ! run_promote "$TARGET"; then
    fail "first promote run exited non-zero on clean target"
fi

# Curated .claude/ tree (representative subset — full coverage would
# couple this test to skill churn).
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

# Byte-equal source check on a skill .md.
if cmp -s "$REPO_ROOT/.claude/skills/claude-sandbox/SKILL.md" \
          "$TARGET/.claude/skills/claude-sandbox/SKILL.md"; then
    pass
else
    fail "promoted claude-sandbox/SKILL.md differs from source"
fi

# Settings merge.
SETTINGS="$TARGET/.claude/settings.json"
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

# Install machinery: byte-equal copies of install.sh, claude-shadow,
# promote.sh. The root `install` shim is intentionally NOT copied —
# promoted repos invoke install.sh directly from postCreate.
expect_byte_equal() {
    local src="$1" dst="$2"
    if cmp -s "$src" "$dst"; then
        pass
    else
        fail "$dst differs from source $src"
    fi
}
expect_byte_equal "$REPO_ROOT/.devcontainer/claude-sandbox/install.sh"    "$TARGET/.devcontainer/claude-sandbox/install.sh"
expect_byte_equal "$REPO_ROOT/.devcontainer/claude-sandbox/claude-shadow" "$TARGET/.devcontainer/claude-sandbox/claude-shadow"
expect_byte_equal "$REPO_ROOT/.devcontainer/claude-sandbox/promote.sh"    "$TARGET/.devcontainer/claude-sandbox/promote.sh"

# Root `install` shim must NOT land in the target.
if [ ! -e "$TARGET/install" ]; then
    pass
else
    fail "root install shim was copied into target (should be source-repo-only)"
fi

# postCreate.sh exists, is executable, contains the install.sh invocation.
PC="$TARGET/.devcontainer/postCreate.sh"
if [ -f "$PC" ] && [ -x "$PC" ]; then
    pass
else
    fail "postCreate.sh missing or not executable"
fi
if grep -Eq '^[[:space:]]*bash[[:space:]]+\.devcontainer/claude-sandbox/install\.sh' "$PC"; then
    pass
else
    fail "postCreate.sh does not contain the install.sh invocation"
fi

# devcontainer.json was not auto-created — promote stays out of that
# file by policy.
if [ ! -f "$TARGET/.devcontainer/devcontainer.json" ]; then
    pass
else
    fail "promote auto-created devcontainer.json (must stay hands-off)"
fi

# The paste-this snippet must always be printed on stderr.
SNIPPET_OUT="$(bash "$PROMOTE" "$TARGET" 2>&1 >/dev/null)"
if printf '%s' "$SNIPPET_OUT" | grep -q '"postCreateCommand": ".devcontainer/postCreate.sh"'; then
    pass
else
    fail "promote did not print the paste-this snippet for devcontainer.json"
fi

# Idempotency: re-run must be byte-stable across the whole target.
SUMS_A="$(cd "$TARGET" && find . -type f -print0 | sort -z | xargs -0 sha256sum)"
if ! run_promote "$TARGET"; then
    fail "second promote run exited non-zero"
fi
SUMS_B="$(cd "$TARGET" && find . -type f -print0 | sort -z | xargs -0 sha256sum)"
if [ "$SUMS_A" = "$SUMS_B" ]; then
    pass
else
    fail "promote re-run drifted the target tree"
fi

# ============================================================
# Section 2: devcontainer.json present — promote must leave it
# untouched regardless of shape (JSONC with comments, JSON with an
# existing postCreateCommand, etc.).
# ============================================================
DC_TARGET="$(mktemp -d)"
trap 'rm -rf "$TARGET" "$DC_TARGET"' EXIT
mkdir -p "$DC_TARGET/.devcontainer"
cat > "$DC_TARGET/.devcontainer/devcontainer.json" <<'JSONC'
// JSONC with a comment so jq can't parse it, and a postCreateCommand
// already set to something else.
{
  "name": "demo",
  "image": "ubuntu:24.04",
  "postCreateCommand": "echo hi"
}
JSONC
DC_SUM_A="$(sha256sum "$DC_TARGET/.devcontainer/devcontainer.json" | awk '{print $1}')"

if ! run_promote "$DC_TARGET"; then
    fail "promote on JSONC-with-existing-cmd target exited non-zero"
fi
DC_SUM_B="$(sha256sum "$DC_TARGET/.devcontainer/devcontainer.json" | awk '{print $1}')"
if [ "$DC_SUM_A" = "$DC_SUM_B" ]; then
    pass
else
    fail "promote modified devcontainer.json (must stay hands-off)"
fi
# Snippet still printed.
DC_OUT="$(bash "$PROMOTE" "$DC_TARGET" 2>&1 >/dev/null)"
if printf '%s' "$DC_OUT" | grep -q '"postCreateCommand": ".devcontainer/postCreate.sh"'; then
    pass
else
    fail "promote on JSONC target did not print the paste-this snippet"
fi

# ============================================================
# Section 3: existing postCreate.sh — append our line, dedup on re-run.
# ============================================================
APPEND_TARGET="$(mktemp -d)"
trap 'rm -rf "$TARGET" "$DC_TARGET" "$APPEND_TARGET"' EXIT
mkdir -p "$APPEND_TARGET/.devcontainer"
cat > "$APPEND_TARGET/.devcontainer/postCreate.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "user setup"
EOF
chmod 0755 "$APPEND_TARGET/.devcontainer/postCreate.sh"

run_promote "$APPEND_TARGET" || fail "promote with existing postCreate.sh exited non-zero"
if grep -q 'echo "user setup"' "$APPEND_TARGET/.devcontainer/postCreate.sh" && \
   grep -Eq '^[[:space:]]*bash[[:space:]]+\.devcontainer/claude-sandbox/install\.sh' \
        "$APPEND_TARGET/.devcontainer/postCreate.sh"; then
    pass
else
    fail "promote did not preserve user postCreate.sh content + append install.sh"
fi

# Re-run: line count must be stable (dedup guard).
LINES_A="$(wc -l < "$APPEND_TARGET/.devcontainer/postCreate.sh")"
run_promote "$APPEND_TARGET" || fail "promote re-run on appended postCreate.sh exited non-zero"
LINES_B="$(wc -l < "$APPEND_TARGET/.devcontainer/postCreate.sh")"
if [ "$LINES_A" = "$LINES_B" ]; then
    pass
else
    fail "promote re-run duplicated install.sh in postCreate.sh (lines went $LINES_A -> $LINES_B)"
fi

# ============================================================
# Section 4: refusals.
# ============================================================
if bash "$PROMOTE" "$REPO_ROOT" >/dev/null 2>&1; then
    fail "promote did not refuse TARGET == REPO_ROOT"
else
    pass
fi

NONEXISTENT="$(mktemp -d)/does-not-exist"
if bash "$PROMOTE" "$NONEXISTENT" >/dev/null 2>&1; then
    fail "promote did not refuse a missing target dir"
else
    pass
fi

echo "promote.sh: $PASSED passed / $FAILED failed"
[ "$FAILED" -eq 0 ]
