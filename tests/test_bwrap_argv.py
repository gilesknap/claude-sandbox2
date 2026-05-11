"""Smoke test for the bash bwrap argv builder.

Sources the bash function and asserts the security-critical substrings
are present in the emitted argv. Full coverage (per-flag matrix, fixture
equality) lands in slice 2.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BWRAP_ARGV_SH = REPO_ROOT / "src" / "claude_sandbox" / "data" / "bwrap_argv.sh"


def _run_builder(
    workspace: str = "/tmp",
    real_claude: str = "/opt/claude/bin/claude",
    fresh_proc: str | None = None,
    home: str | None = None,
) -> str:
    """Source bwrap_argv.sh and emit the argv for (workspace, real_claude).

    `fresh_proc` sets `CLAUDE_SANDBOX_FRESH_PROC` before invoking the
    builder, so callers can exercise both the secure default (fresh
    procfs) and the degraded bind-/proc fallback in one helper.
    `home` overrides `$HOME` for tests that need to stage credential
    dirs under a temporary home.
    """
    env_prefix = ""
    if fresh_proc is not None:
        env_prefix += f"export CLAUDE_SANDBOX_FRESH_PROC={fresh_proc}\n"
    if home is not None:
        env_prefix += f"export HOME={home}\n"
    script = (
        "set -eu\n"
        f"{env_prefix}"
        f"source {BWRAP_ARGV_SH}\n"
        f'bwrap_argv_build "{workspace}" "{real_claude}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_argv_starts_with_bwrap() -> None:
    argv = _run_builder()
    assert argv.splitlines()[0] == "bwrap"


def test_argv_contains_security_critical_flags() -> None:
    argv = _run_builder()
    # Strict-under-/root + namespaces + cap drop + new session.
    for flag in (
        "--ro-bind",
        "--tmpfs",
        "--cap-drop",
        "ALL",
        "--unshare-user-try",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--new-session",
        "--die-with-parent",
        "--clearenv",
        "--proc",
        "/proc",
    ):
        assert flag in argv, f"missing security-critical token in argv: {flag!r}"


def test_argv_fresh_proc_default_uses_proc_mount() -> None:
    """Default mode mounts a fresh procfs; bind-/proc is NOT used."""
    argv = _run_builder()
    lines = argv.splitlines()
    assert "--proc" in lines, "expected --proc flag in default mode"
    # The bind-/proc pair (`--ro-bind /proc /proc`) must not show up in
    # the secure default — only the fresh-proc mount.
    pairs = list(zip(lines, lines[1:], lines[2:], strict=False))
    bind_proc_pair = ("--ro-bind", "/proc", "/proc")
    assert bind_proc_pair not in pairs, (
        "unexpected --ro-bind /proc /proc in default mode"
    )


def test_argv_fresh_proc_disabled_uses_bind_mount() -> None:
    """CLAUDE_SANDBOX_FRESH_PROC=0 swaps --proc for --ro-bind /proc /proc."""
    argv = _run_builder(fresh_proc="0")
    lines = argv.splitlines()
    pairs = list(zip(lines, lines[1:], lines[2:], strict=False))
    bind_proc_pair = ("--ro-bind", "/proc", "/proc")
    assert bind_proc_pair in pairs, "expected --ro-bind /proc /proc fallback"
    # And the fresh-proc mount must NOT also be present — the two modes
    # are mutually exclusive (bwrap would conflict otherwise).
    proc_pairs = [
        (a, b) for a, b in zip(lines, lines[1:], strict=False) if a == "--proc"
    ]
    assert proc_pairs == [], f"unexpected --proc mount in degraded mode: {proc_pairs}"


def test_argv_masks_run_secrets() -> None:
    """tmpfs over /run/secrets closes the Docker/Compose secrets path.

    The mask only fires when the host has /run/secrets — in nested
    containers /run can be read-only and the parent may not have the
    subdir, in which case bwrap would fail to mkdir the mount point.
    The check is informative either way: present when needed, absent
    when there's nothing to mask.
    """
    argv = _run_builder()
    if Path("/run/secrets").is_dir():
        assert "/run/secrets" in argv
    else:
        assert "/run/secrets" not in argv


def test_argv_sets_is_sandbox_sentinel() -> None:
    argv = _run_builder()
    # IS_SANDBOX=1 must be set so the recursion guard + the hook see it.
    assert "IS_SANDBOX" in argv
    assert "GIT_CONFIG_GLOBAL" in argv


def test_argv_ends_with_real_claude() -> None:
    argv = _run_builder(real_claude="/opt/claude/bin/claude")
    lines = argv.strip().splitlines()
    # The argv must terminate with `-- <real_claude>` so any forwarded
    # args land on Claude, not on bwrap.
    assert lines[-1] == "/opt/claude/bin/claude"
    assert "--" in lines


def test_argv_binds_gh_and_glab_when_present(tmp_path) -> None:
    """gh / glab credential dirs are re-bound through the strict-/root
    inversion when they exist on the host. Without these binds the user's
    forge tokens would be invisible inside the sandbox and `gh auth status`
    would report "not authenticated".
    """
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".config" / "gh").mkdir(parents=True)
    (tmp_path / ".config" / "glab-cli").mkdir(parents=True)
    argv = _run_builder(home=str(tmp_path))
    assert f"{tmp_path}/.config/gh" in argv
    assert f"{tmp_path}/.config/glab-cli" in argv


def test_argv_omits_credential_binds_when_absent(tmp_path) -> None:
    """When the host has no gh/glab config dirs, the binds must be
    absent — otherwise bwrap would fail to launch on a fresh host."""
    (tmp_path / ".claude").mkdir()
    argv = _run_builder(home=str(tmp_path))
    assert f"{tmp_path}/.config/gh" not in argv
    assert f"{tmp_path}/.config/glab-cli" not in argv


def test_argv_does_not_bind_arbitrary_config_subdirs(tmp_path) -> None:
    """Siblings under ~/.config (VS Code, etc.) stay invisible — only
    the explicit gh/glab-cli allowlist is exposed."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".config" / "Code").mkdir(parents=True)
    (tmp_path / ".config" / "git-credential-vscode").mkdir(parents=True)
    argv = _run_builder(home=str(tmp_path))
    assert f"{tmp_path}/.config/Code" not in argv
    assert f"{tmp_path}/.config/git-credential-vscode" not in argv


def test_argv_binds_claude_json_when_present(tmp_path) -> None:
    """Claude Code's account-state file ~/.claude.json holds the OAuth
    token. Without re-binding it through the strict-under-/root tmpfs,
    every fresh `claude` launch starts unauth'd and the user re-logs in.
    """
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").touch()
    argv = _run_builder(home=str(tmp_path))
    assert f"{tmp_path}/.claude.json" in argv


def test_argv_omits_claude_json_bind_when_absent(tmp_path) -> None:
    """If the host has no ~/.claude.json yet (first-ever launch from a
    raw container fs), the bind must be skipped — otherwise bwrap fails.
    The shadow's `touch` pre-creates the file before launch so this
    branch is unreachable in production, but the builder must stay
    safe when called from tests / probes."""
    (tmp_path / ".claude").mkdir()
    argv = _run_builder(home=str(tmp_path))
    assert f"{tmp_path}/.claude.json" not in argv


def test_argv_binds_uv_when_present(tmp_path) -> None:
    """uv-managed Python interpreters at ~/.local/share/uv plus the
    `uv` / `uvx` tool binaries at ~/.local/bin must be bound through
    the strict-/root inversion. Without these binds the project's
    `.venv/bin/python` symlink resolves to nothing inside the sandbox
    and any `uv run` / `.venv/bin/*` invocation fails.
    """
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".local" / "share" / "uv").mkdir(parents=True)
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / "uv").touch()
    (tmp_path / ".local" / "bin" / "uvx").touch()
    argv = _run_builder(home=str(tmp_path))
    assert f"{tmp_path}/.local/share/uv" in argv
    assert f"{tmp_path}/.local/bin/uv" in argv
    assert f"{tmp_path}/.local/bin/uvx" in argv
    # PATH must append $HOME/.local/bin (after system paths, never before).
    assert f":{tmp_path}/.local/bin" in argv


def test_argv_omits_uv_binds_when_absent(tmp_path) -> None:
    """When the host has no uv installed, the binds must be absent —
    otherwise bwrap would fail to launch on a non-uv host."""
    (tmp_path / ".claude").mkdir()
    argv = _run_builder(home=str(tmp_path))
    assert f"{tmp_path}/.local/share/uv" not in argv
    assert f"{tmp_path}/.local/bin/uv" not in argv


def test_argv_does_not_bind_other_local_bin_entries(tmp_path) -> None:
    """Only `uv` / `uvx` are explicitly bound under ~/.local/bin.
    Other binaries the user has installed there (cargo-installed
    tools, pipx envs, etc.) stay invisible — otherwise we'd be
    re-exposing the whole ~/.local/bin tree."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / "cargo-something").touch()
    (tmp_path / ".local" / "bin" / "pipx").touch()
    argv = _run_builder(home=str(tmp_path))
    assert f"{tmp_path}/.local/bin/cargo-something" not in argv
    assert f"{tmp_path}/.local/bin/pipx" not in argv
