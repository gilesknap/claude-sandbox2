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


def _run_builder(workspace: str = "/tmp", real_claude: str = "/opt/claude/bin/claude") -> str:
    """Source bwrap_argv.sh and emit the argv for (workspace, real_claude)."""
    script = f'set -eu\nsource {BWRAP_ARGV_SH}\nbwrap_argv_build "{workspace}" "{real_claude}"\n'
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


def test_argv_masks_run_secrets() -> None:
    """tmpfs over /run/secrets closes the Docker/Compose secrets path."""
    argv = _run_builder()
    assert "/run/secrets" in argv


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
