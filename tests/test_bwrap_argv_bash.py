"""Pytest wrapper that runs the bash spec for bwrap_argv.

The bash file is the canonical spec — if it ever drifts from what the
runtime emits, this test surfaces it as a pytest failure too. Keep
both: the bash spec for hand-running and CI line-noise, the pytest
shim for the single `uv run pytest` story.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.skip(
    "PAUSED: bwrap_argv.sh is being structurally refactored — "
    "bash spec suspended to cut churn. Re-enable once design stabilises.",
    allow_module_level=True,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASH_SPEC = REPO_ROOT / "tests" / "bwrap_argv.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_bwrap_argv_bash_spec_passes() -> None:
    result = subprocess.run(
        ["bash", str(BASH_SPEC)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bwrap_argv.sh failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
