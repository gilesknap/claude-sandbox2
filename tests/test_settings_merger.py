"""End-to-end coverage for settings_merger: pure-merge, file-merge,
clean install, idempotent re-merge, conflict refusal, and the
never-touch-non-target-keys invariant.

The slice-1 `test_settings_merge.py` covered the in-memory merge
function via its slice-1 import path (`installer.merge_user_prompt_submit_hook`).
This file targets the slice-2 module surface (`settings_merger.merge`,
`settings_merger.merge_file`) and adds the on-disk fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_sandbox import settings_merger
from claude_sandbox.installer import OUR_HOOK_BLOCK, place_workspace_settings
from claude_sandbox.settings_merger import (
    SettingsConflictError,
    merge,
    merge_file,
)


def test_clean_install_into_empty_dict() -> None:
    merged = merge({}, OUR_HOOK_BLOCK)
    assert merged == {"hooks": {"UserPromptSubmit": [OUR_HOOK_BLOCK]}}


def test_idempotent_remerge() -> None:
    once = merge({}, OUR_HOOK_BLOCK)
    twice = merge(once, OUR_HOOK_BLOCK)
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)


def test_never_touch_non_target_keys() -> None:
    """Every key that isn't `hooks.UserPromptSubmit` must come back
    byte-identical. This is the load-bearing invariant — the user's
    permissions / env / additionalDirectories are entirely theirs.
    """
    existing = {
        "permissions": {"allow": ["Bash(*)"], "deny": ["Bash(sudo *)"]},
        "env": {"FOO": "bar", "BAZ": "qux"},
        "additionalDirectories": ["/tmp", "/srv"],
        "hooks": {
            # Other hook lifecycle keys must also be preserved.
            "PostToolUse": [{"matcher": "*", "command": "/bin/true"}],
        },
    }
    merged = merge(existing, OUR_HOOK_BLOCK)
    assert merged["permissions"] == existing["permissions"]
    assert merged["env"] == existing["env"]
    assert merged["additionalDirectories"] == existing["additionalDirectories"]
    assert merged["hooks"]["PostToolUse"] == existing["hooks"]["PostToolUse"]
    assert merged["hooks"]["UserPromptSubmit"] == [OUR_HOOK_BLOCK]


def test_conflict_refusal_raises_typed_exception() -> None:
    """Same hook script with a different command string -> hard refuse."""
    conflicting = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/usr/local/bin/sandbox-check.sh --strict",
                        }
                    ]
                }
            ]
        }
    }
    with pytest.raises(SettingsConflictError) as excinfo:
        merge(conflicting, OUR_HOOK_BLOCK)
    assert "sandbox-check.sh" in str(excinfo.value)
    # The diagnostic must hint at what the user should do.
    assert "Reconcile" in str(excinfo.value) or "reconcile" in str(excinfo.value)


def test_appends_alongside_unrelated_user_prompt_submit_hook() -> None:
    other = {
        "hooks": [
            {"type": "command", "command": ".claude/hooks/log-prompt.sh"},
        ]
    }
    existing = {"hooks": {"UserPromptSubmit": [other]}}
    merged = merge(existing, OUR_HOOK_BLOCK)
    assert merged["hooks"]["UserPromptSubmit"] == [other, OUR_HOOK_BLOCK]


def test_merge_file_clean_install(tmp_path: Path) -> None:
    """A non-existent file is treated as `merge({}, ours)`."""
    settings = tmp_path / "settings.json"
    merged = merge_file(settings, OUR_HOOK_BLOCK)
    assert merged == {"hooks": {"UserPromptSubmit": [OUR_HOOK_BLOCK]}}


def test_merge_file_empty_file_treated_as_empty_dict(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("")
    merged = merge_file(settings, OUR_HOOK_BLOCK)
    assert merged == {"hooks": {"UserPromptSubmit": [OUR_HOOK_BLOCK]}}


def test_merge_file_invalid_json_refuses(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{not json")
    with pytest.raises(SettingsConflictError) as excinfo:
        merge_file(settings, OUR_HOOK_BLOCK)
    assert "valid JSON" in str(excinfo.value)


def test_place_workspace_settings_idempotent_on_disk(tmp_path: Path) -> None:
    """End-to-end: placing settings twice yields a byte-identical file."""
    place_workspace_settings(tmp_path)
    first = (tmp_path / ".claude" / "settings.json").read_bytes()
    place_workspace_settings(tmp_path)
    second = (tmp_path / ".claude" / "settings.json").read_bytes()
    assert first == second


def test_target_key_path_constant_documents_scope() -> None:
    """Regression guard: if anyone broadens the merger, this constant must change."""
    assert settings_merger.TARGET_KEY_PATH == ("hooks", "UserPromptSubmit")
