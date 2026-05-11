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
) -> str:
    """Source bwrap_argv.sh and emit the argv for (workspace, real_claude).

    `fresh_proc` sets `CLAUDE_SANDBOX_FRESH_PROC` before invoking the
    builder, so callers can exercise both the secure default (fresh
    procfs) and the degraded bind-/proc fallback in one helper.
    """
    env_prefix = ""
    if fresh_proc is not None:
        env_prefix = f"export CLAUDE_SANDBOX_FRESH_PROC={fresh_proc}\n"
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
    assert bind_proc_pair not in pairs, "unexpected --ro-bind /proc /proc in default mode"


def test_argv_fresh_proc_disabled_uses_bind_mount() -> None:
    """CLAUDE_SANDBOX_FRESH_PROC=0 swaps --proc for --ro-bind /proc /proc."""
    argv = _run_builder(fresh_proc="0")
    lines = argv.splitlines()
    pairs = list(zip(lines, lines[1:], lines[2:], strict=False))
    bind_proc_pair = ("--ro-bind", "/proc", "/proc")
    assert bind_proc_pair in pairs, "expected --ro-bind /proc /proc fallback"
    # And the fresh-proc mount must NOT also be present — the two modes
    # are mutually exclusive (bwrap would conflict otherwise).
    proc_pairs = [(a, b) for a, b in zip(lines, lines[1:], strict=False) if a == "--proc"]
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
