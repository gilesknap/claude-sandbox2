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
    link_claude_to_terminal_config,
    place_workspace_hook,
    place_workspace_settings,
    real_claude_path,
)


def _stage_fake_clone(root: Path) -> Path:
    """Create a minimal directory tree that `_resolve_src_dir_strict`
    will accept as a claude-sandbox clone.

    The strict check requires pyproject.toml + src/claude_sandbox/ to
    exist. We don't need to populate them with anything — the installer
    only checks for their presence when resolving src_dir.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("")
    (root / "src" / "claude_sandbox").mkdir(parents=True)
    return root


def test_dry_run_returns_plan_without_touching_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run must list every container + workspace target and never write."""
    src = _stage_fake_clone(tmp_path / "src")
    monkeypatch.setenv("CLAUDE_SANDBOX_SRC_DIR", str(src))
    plan = installer.install(workspace=tmp_path / "ws", dry_run=True)
    assert isinstance(plan, DryRunPlan)

    # Every required container target must be in the plan.
    container_targets = {p for p, _ in plan.container_files}
    assert installer.SHADOW_CLAUDE_PATH in container_targets
    assert installer.SHADOW_CLI_PATH in container_targets
    assert real_claude_path(src) in container_targets
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

    src = _stage_fake_clone(tmp_path / "src")
    monkeypatch.setenv("CLAUDE_SANDBOX_SRC_DIR", str(src))
    monkeypatch.setattr(
        probe, "mount_scan", lambda: ["claude-sandbox: warning — /kubeconfig"]
    )
    plan = installer.install(workspace=tmp_path / "ws", dry_run=True)
    assert plan is not None and any("/kubeconfig" in w for w in plan.warnings)


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
    assert (
        dst.read_bytes()
        == (repo_root / ".claude" / "hooks" / "sandbox-check.sh").read_bytes()
    )


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


# place_real_claude — shadow-at-target detection
#
# Regression: a previous installer run that found a shadow on $PATH could
# copy the shadow itself into the destination; the shadow then exec'd
# itself in an infinite loop on every launch (issue #N). place_real_claude
# must (a) recognise a shadow parked at the target and replace it, and
# (b) never copy a shadow as the "real" binary in the first place.


def _shadow_text() -> str:
    """Minimal shadow-shaped script for tests."""
    return "#!/usr/bin/env bash\nset -e\nIS_SANDBOX=1\nexec bwrap_argv_build\n"


def _fake_real_binary_bytes() -> bytes:
    """Bytes that mimic an ELF/Bun binary header — anything without the sentinel."""
    return b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 1024


def test_looks_like_shadow_detects_shadow_template(repo_root: Path) -> None:
    """The shipped shadow template itself must be recognised as a shadow."""
    template = repo_root / "src" / "claude_sandbox" / "data" / "claude-shadow"
    assert installer._looks_like_shadow(template)


def test_looks_like_shadow_rejects_real_binary(tmp_path: Path) -> None:
    fake = tmp_path / "claude"
    fake.write_bytes(_fake_real_binary_bytes())
    assert not installer._looks_like_shadow(fake)


def test_place_real_claude_replaces_shadow_at_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the destination already holds a shadow, replace it from resolved source."""
    src_dir = tmp_path / "clone"
    src_dir.mkdir()
    target = real_claude_path(src_dir)
    target.parent.mkdir(parents=True)
    target.write_text(_shadow_text())
    target.chmod(0o755)

    source = tmp_path / "real-claude"
    source.write_bytes(_fake_real_binary_bytes())
    monkeypatch.setattr(installer, "_resolve_real_claude_source", lambda: source)

    installer.place_real_claude(src_dir)

    assert target.read_bytes() == _fake_real_binary_bytes()
    assert target.stat().st_mode & 0o777 == 0o755


def test_place_real_claude_is_noop_when_target_is_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing real binary at the target must be left alone (idempotency)."""
    src_dir = tmp_path / "clone"
    src_dir.mkdir()
    target = real_claude_path(src_dir)
    target.parent.mkdir(parents=True)
    target.write_bytes(_fake_real_binary_bytes())
    target.chmod(0o755)

    def _should_not_be_called() -> Path:
        raise AssertionError("_resolve_real_claude_source called on idempotent path")

    monkeypatch.setattr(installer, "_resolve_real_claude_source", _should_not_be_called)
    installer.place_real_claude(src_dir)
    assert target.read_bytes() == _fake_real_binary_bytes()


def test_resolve_real_claude_source_skips_shadow_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `which claude` resolves to a shadow, the resolver must refuse it."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    # ~/.local/bin/claude does NOT exist here — we want to exercise the
    # shutil.which fallback.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    shadow_on_path = tmp_path / "usr" / "local" / "bin" / "claude"
    shadow_on_path.parent.mkdir(parents=True)
    shadow_on_path.write_text(_shadow_text())
    shadow_on_path.chmod(0o755)
    monkeypatch.setattr(installer.shutil, "which", lambda _name: str(shadow_on_path))

    assert installer._resolve_real_claude_source() is None


def test_resolve_real_claude_source_follows_home_local_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anthropic layout: ~/.local/bin/claude -> ~/.local/share/claude/versions/X.Y.Z."""
    home = tmp_path / "home"
    versions_dir = home / ".local" / "share" / "claude" / "versions"
    versions_dir.mkdir(parents=True)
    real = versions_dir / "2.1.138"
    real.write_bytes(_fake_real_binary_bytes())
    real.chmod(0o755)

    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "claude").symlink_to(real)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    resolved = installer._resolve_real_claude_source()
    assert resolved == real.resolve()


# ---------------------------------------------------------------------------
# link_claude_to_terminal_config — shared /user-terminal-config/.claude
# ---------------------------------------------------------------------------


def test_link_claude_noop_when_target_missing(tmp_path: Path) -> None:
    """No /user-terminal-config/.claude on this host → silent no-op."""
    link = tmp_path / "home" / ".claude"
    target = tmp_path / "no-such-dir"
    link_claude_to_terminal_config(target=target, link=link)
    assert not link.exists()


def test_link_claude_creates_symlink_when_target_exists(tmp_path: Path) -> None:
    target = tmp_path / "user-terminal-config" / ".claude"
    target.mkdir(parents=True)
    link = tmp_path / "home" / ".claude"

    link_claude_to_terminal_config(target=target, link=link)
    assert link.is_symlink()
    assert link.resolve() == target.resolve()


def test_link_claude_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "user-terminal-config" / ".claude"
    target.mkdir(parents=True)
    link = tmp_path / "home" / ".claude"

    link_claude_to_terminal_config(target=target, link=link)
    link_claude_to_terminal_config(target=target, link=link)  # second call is a no-op
    assert link.resolve() == target.resolve()


def test_link_claude_refuses_to_clobber_real_dir(tmp_path: Path) -> None:
    target = tmp_path / "user-terminal-config" / ".claude"
    target.mkdir(parents=True)
    link = tmp_path / "home" / ".claude"
    link.mkdir(parents=True)
    (link / "settings.json").write_text("{}")

    with pytest.raises(SettingsConflictError, match="real directory"):
        link_claude_to_terminal_config(target=target, link=link)
    # The existing dir + its contents must survive.
    assert (link / "settings.json").read_text() == "{}"


def test_link_claude_refuses_to_clobber_wrong_symlink(tmp_path: Path) -> None:
    target = tmp_path / "user-terminal-config" / ".claude"
    target.mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    link = tmp_path / "home" / ".claude"
    link.parent.mkdir(parents=True)
    link.symlink_to(other)

    with pytest.raises(SettingsConflictError, match="symlink"):
        link_claude_to_terminal_config(target=target, link=link)
    assert link.resolve() == other.resolve()
