"""gitconfig: render is a pure function (mock subprocess for `generate`),
atomic write means no half-written file is ever observable.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from claude_sandbox import gitconfig

REPO_ROOT = Path(__file__).resolve().parent.parent
SHADOW_PATH = REPO_ROOT / "src" / "claude_sandbox" / "data" / "claude-shadow"


def test_render_includes_user_section() -> None:
    body = gitconfig.render("Ada Lovelace", "ada@example.com")
    assert "[user]" in body
    assert "name = Ada Lovelace" in body
    assert "email = ada@example.com" in body


def test_render_registers_gh_credential_helper() -> None:
    body = gitconfig.render("x", "x@x")
    # The credential helper line is the load-bearing one for `git push`
    # to GitHub working from inside the sandbox without an OAuth popup.
    assert '[credential "https://github.com"]' in body
    assert "helper = !gh auth git-credential" in body


def test_render_sets_init_default_branch_main() -> None:
    body = gitconfig.render("x", "x@x")
    assert "[init]" in body
    assert "defaultBranch = main" in body


def test_render_marks_all_dirs_safe() -> None:
    """bwrap's UID remap commonly trips git's safe.directory check."""
    body = gitconfig.render("x", "x@x")
    assert "[safe]" in body
    assert "directory = *" in body


def test_render_is_pure() -> None:
    """Same input -> same bytes, every time."""
    a = gitconfig.render("Ada", "ada@example.com")
    b = gitconfig.render("Ada", "ada@example.com")
    assert a == b


def test_generate_uses_host_git_config_when_args_omitted(tmp_path: Path) -> None:
    out = tmp_path / "claude-gitconfig"

    def fake_git_config(*args, **kwargs):
        # Mimic `git config --get user.{name,email}` returning known values.
        cmd = args[0]
        result = MagicMock()
        result.returncode = 0
        if cmd[-1] == "user.name":
            result.stdout = "Ada Lovelace\n"
        elif cmd[-1] == "user.email":
            result.stdout = "ada@example.com\n"
        else:
            result.stdout = ""
        return result

    with patch("claude_sandbox.gitconfig.subprocess.run", side_effect=fake_git_config):
        gitconfig.generate(out_path=out)

    body = out.read_text()
    assert "name = Ada Lovelace" in body
    assert "email = ada@example.com" in body


def test_generate_overrides_take_precedence(tmp_path: Path) -> None:
    out = tmp_path / "claude-gitconfig"
    with patch("claude_sandbox.gitconfig.subprocess.run") as run_mock:
        gitconfig.generate(name="Override", email="override@example.com", out_path=out)
        # Subprocess should NOT have been consulted at all when both
        # overrides are passed.
        run_mock.assert_not_called()
    assert "name = Override" in out.read_text()
    assert "email = override@example.com" in out.read_text()


def test_generate_atomic_write_no_partial_file(tmp_path: Path) -> None:
    """A failed write must not leave a partial file behind.

    We patch os.replace to raise, then assert that the only entries in
    the directory are the original (none) — no .claude-gitconfig.* tmp
    file leaked, no claude-gitconfig partial.
    """
    out = tmp_path / "claude-gitconfig"
    with (
        patch("claude_sandbox.gitconfig.os.replace", side_effect=OSError("disk full")),
        contextlib.suppress(OSError),
    ):
        gitconfig.generate(name="x", email="x@x", out_path=out)
    # Output file must not exist, and no leftover tmp files.
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


def test_shadow_heredoc_matches_python_render() -> None:
    """The claude-shadow's per-launch refresh re-renders /etc/claude-gitconfig
    from a bash here-doc. That here-doc and gitconfig.render() must produce
    byte-identical output for the same (name, email) — otherwise the install-
    time write and the per-launch refresh disagree and tests assert against
    a template that diverges from the runtime reality.
    """
    shadow = SHADOW_PATH.read_text()
    match = re.search(
        r'cat > "\$CLAUDE_SANDBOX_GITCONFIG_PATH" <<EOF\n(?P<body>.*?)\nEOF\n',
        shadow,
        re.DOTALL,
    )
    assert match, "could not locate the gitconfig here-doc in claude-shadow"

    # Run the here-doc through bash with controlled $git_name / $git_email
    # so the comparison is on the exact bytes Claude's launch path would
    # produce. Substituting with Python's .format would miss any bash-only
    # quoting subtlety.
    body = match.group("body")
    script = (
        f'git_name="Ada Lovelace"\ngit_email="ada@example.com"\ncat <<EOF\n{body}\nEOF'
    )
    rendered = subprocess.run(
        ["bash", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert rendered == gitconfig.render("Ada Lovelace", "ada@example.com")


def test_generate_full_body_byte_for_byte(tmp_path: Path) -> None:
    out = tmp_path / "claude-gitconfig"
    gitconfig.generate(name="A", email="a@a", out_path=out)
    expected = (
        "[user]\n"
        "    name = A\n"
        "    email = a@a\n"
        '[credential "https://github.com"]\n'
        "    helper = !gh auth git-credential\n"
        "[init]\n"
        "    defaultBranch = main\n"
        "[safe]\n"
        "    directory = *\n"
    )
    assert out.read_text() == expected
