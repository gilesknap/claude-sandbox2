"""Markdown-fence extraction is a pure function. Given a fixture spec
string and a check number N, we get back the bash body.
"""

from __future__ import annotations

import pytest

from claude_sandbox.verifier import CHECK_NAMES, TOTAL_CHECKS, extract_check


def test_extract_check_returns_body_for_check_one(verify_spec_text: str) -> None:
    body = extract_check(verify_spec_text, 1)
    # The known check 01 body is a single-line IS_SANDBOX assertion.
    assert body.strip() == '[ "${IS_SANDBOX:-}" = "1" ]'


def test_extract_check_returns_multiline_body(verify_spec_text: str) -> None:
    body = extract_check(verify_spec_text, 2)
    # Check 02 is a multi-line case statement.
    assert "case " in body
    assert "/proc/1/comm" in body
    assert "bwrap|claude|node" in body


def test_extract_check_handles_high_numbered_check(verify_spec_text: str) -> None:
    body = extract_check(verify_spec_text, 18)
    assert "GIT_CONFIG_GLOBAL" in body
    assert "git config --get user.email" in body


def test_extract_check_returns_empty_for_unknown_check(verify_spec_text: str) -> None:
    # 99 doesn't exist; the extractor returns an empty string (the
    # runner converts that to a FAIL).
    assert extract_check(verify_spec_text, 99) == ""


@pytest.mark.parametrize("n", range(1, TOTAL_CHECKS + 1))
def test_every_check_in_spec_has_extractable_body(verify_spec_text: str, n: int) -> None:
    """Spec invariant: every numbered check from 1..18 has a bash body."""
    body = extract_check(verify_spec_text, n)
    assert body, f"check {n:02d} ({CHECK_NAMES[n - 1]}) has no extractable body"
