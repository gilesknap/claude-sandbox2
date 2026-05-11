"""installer: dry-run plan records all placements without touching disk;
workspace settings + hook end-to-end on a tmpdir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_sandbox import installer
from claude_sandbox.installer import (
    OUR_HOOK_BLOCK,
    DryRunPlan,
    SettingsConflictError,
    place_workspace_hook,
    place_workspace_settings,
)


def test_dry_run_returns_plan_without_touching_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run must list every container + workspace target and never write."""
    monkeypatch.setenv("CLAUDE_SANDBOX_SRC_DIR", str(tmp_path / "src"))
    plan = installer.install(workspace=tmp_path / "ws", dry_run=True)
    assert isinstance(plan, DryRunPlan)

    # Every container target the PRD calls out must be in the plan.
    container_targets = {p for p, _ in plan.container_files}
    assert installer.SHADOW_CLAUDE_PATH in container_targets
    assert installer.SHADOW_CLI_PATH in container_targets
    assert installer.REAL_CLAUDE_PATH in container_targets
    assert installer.GITCONFIG_PATH in container_targets

    # Workspace targets: settings.json + sandbox-check.sh hook.
    ws_targets = {p for p, _ in plan.workspace_files}
    assert (tmp_path / "ws" / ".claude" / "settings.json") in ws_targets
    assert (tmp_path / "ws" / ".claude" / "hooks" / "sandbox-check.sh") in ws_targets

    # Disk under workspace must be untouched.
    assert not (tmp_path / "ws").exists()


def test_dry_run_carries_mount_scan_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run still surfaces mount-scan warnings — the install would print them too."""
    # Replace probe.mount_scan with one that returns a fixed warning,
    # so the dry-run path picks it up without touching the real `mount`
    # output.
    from claude_sandbox import probe

    monkeypatch.setattr(probe, "mount_scan", lambda: ["claude-sandbox: warning — /kubeconfig"])
    plan = installer.install(workspace=tmp_path / "ws", dry_run=True)
    assert any("/kubeconfig" in w for w in plan.warnings)


def test_place_workspace_settings_clean_install(tmp_path: Path) -> None:
    place_workspace_settings(tmp_path)
    settings = (tmp_path / ".claude" / "settings.json").read_text()
    assert "sandbox-check.sh" in settings
    # Resulting file ends with a newline (json.dumps + "\n").
    assert settings.endswith("\n")


def test_place_workspace_settings_idempotent(tmp_path: Path) -> None:
    place_workspace_settings(tmp_path)
    once = (tmp_path / ".claude" / "settings.json").read_bytes()
    place_workspace_settings(tmp_path)
    assert (tmp_path / ".claude" / "settings.json").read_bytes() == once


def test_place_workspace_settings_preserves_unrelated_keys(tmp_path: Path) -> None:
    """The user has custom permissions; we must leave them alone."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('{"permissions": {"allow": ["Bash(*)"]}}')
    place_workspace_settings(tmp_path)
    text = settings_path.read_text()
    assert "permissions" in text
    assert "sandbox-check.sh" in text


def test_place_workspace_hook_copy_if_missing(tmp_path: Path, repo_root: Path) -> None:
    place_workspace_hook(tmp_path, repo_root)
    dst = tmp_path / ".claude" / "hooks" / "sandbox-check.sh"
    assert dst.is_file()
    assert dst.read_bytes() == (repo_root / ".claude" / "hooks" / "sandbox-check.sh").read_bytes()


def test_place_workspace_hook_idempotent(tmp_path: Path, repo_root: Path) -> None:
    place_workspace_hook(tmp_path, repo_root)
    place_workspace_hook(tmp_path, repo_root)  # second call is a no-op


def test_place_workspace_hook_refuses_on_diff(tmp_path: Path, repo_root: Path) -> None:
    place_workspace_hook(tmp_path, repo_root)
    dst = tmp_path / ".claude" / "hooks" / "sandbox-check.sh"
    dst.write_text("user edited the hook\n")
    with pytest.raises(SettingsConflictError):
        place_workspace_hook(tmp_path, repo_root)


def test_our_hook_block_is_correctly_shaped() -> None:
    """OUR_HOOK_BLOCK must follow Claude's hook-block shape."""
    inner = OUR_HOOK_BLOCK["hooks"]
    assert isinstance(inner, list)
    assert inner[0]["type"] == "command"
    assert inner[0]["command"].endswith("sandbox-check.sh")
