"""Common pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repo root (the worktree this code lives in)."""
    return REPO_ROOT


@pytest.fixture
def verify_spec_text(repo_root: Path) -> str:
    """The shipped verify-sandbox.md as a string."""
    return (repo_root / ".claude" / "commands" / "verify-sandbox.md").read_text()
