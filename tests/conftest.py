"""Common pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repo root (the worktree this code lives in)."""
    return REPO_ROOT
