"""The inline one-key settings merger: clean install, idempotent
re-merge, refusal on real conflict, and never-touch-other-keys.
"""

from __future__ import annotations

import json

import pytest

from claude_sandbox.installer import (
    SettingsConflictError,
    merge_user_prompt_submit_hook,
)

OUR_HOOK_BLOCK = {
    "hooks": [
        {
            "type": "command",
            "command": ".claude/hooks/sandbox-check.sh",
        }
    ]
}


def test_clean_install_into_empty_settings() -> None:
    merged = merge_user_prompt_submit_hook({}, OUR_HOOK_BLOCK)
    assert merged == {"hooks": {"UserPromptSubmit": [OUR_HOOK_BLOCK]}}


def test_idempotent_remerge_yields_byte_equal_result() -> None:
    """Re-running the merger against an already-merged file must not grow
    the UserPromptSubmit array.
    """
    once = merge_user_prompt_submit_hook({}, OUR_HOOK_BLOCK)
    twice = merge_user_prompt_submit_hook(once, OUR_HOOK_BLOCK)
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)


def test_merge_preserves_existing_unrelated_keys() -> None:
    existing = {
        "permissions": {
            "allow": ["Bash(*)"],
            "deny": ["Bash(sudo *)"],
        },
        "env": {"FOO": "bar"},
        "additionalDirectories": ["/tmp"],
    }
    merged = merge_user_prompt_submit_hook(existing, OUR_HOOK_BLOCK)
    # Every original key is preserved, byte-identical.
    assert merged["permissions"] == existing["permissions"]
    assert merged["env"] == existing["env"]
    assert merged["additionalDirectories"] == existing["additionalDirectories"]
    # And our hook is wired.
    assert merged["hooks"]["UserPromptSubmit"] == [OUR_HOOK_BLOCK]


def test_merge_refuses_on_conflicting_command_for_same_hook_script() -> None:
    """If the user has already wired sandbox-check.sh with a different
    command (e.g. they moved the script), refuse rather than silently
    win.
    """
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
        merge_user_prompt_submit_hook(conflicting, OUR_HOOK_BLOCK)
    assert "sandbox-check.sh" in str(excinfo.value)


def test_merge_appends_alongside_other_user_prompt_submit_hooks() -> None:
    """User has unrelated UserPromptSubmit hook(s); ours is appended,
    theirs untouched.
    """
    other_hook_block = {
        "hooks": [
            {
                "type": "command",
                "command": ".claude/hooks/log-prompt.sh",
            }
        ]
    }
    existing = {"hooks": {"UserPromptSubmit": [other_hook_block]}}
    merged = merge_user_prompt_submit_hook(existing, OUR_HOOK_BLOCK)
    assert merged["hooks"]["UserPromptSubmit"] == [other_hook_block, OUR_HOOK_BLOCK]
