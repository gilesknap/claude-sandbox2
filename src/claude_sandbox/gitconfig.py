"""Generate the curated `/etc/claude-gitconfig` from host identity.

The host's `$HOME/.gitconfig` is invisible to the sandbox via
strict-under-/root, and `/etc/gitconfig` is neutralised by
`GIT_CONFIG_SYSTEM=/dev/null` exported alongside
`GIT_CONFIG_GLOBAL=/etc/claude-gitconfig`. This module produces the
curated file that ends up in effect: host identity for commit
attribution, gh/glab credential helpers, and ssh→https rewrites so
SSH-shaped clone URLs work without an SSH agent in the sandbox.

Why a separate module: the prior bash project's gitconfig generator
(`lib/gitconfig_generator.sh`) lived in shell with a here-doc; in
Python it's an atomic write + a couple of `git config --get` reads.
Pulling it out of `installer.py` keeps the orchestrator focused on
sequencing and gives us one clean target to test (mocked subprocess in,
exact bytes out).
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from pathlib import Path

GITCONFIG_PATH_DEFAULT = Path("/etc/claude-gitconfig")


def generate(
    name: str | None = None,
    email: str | None = None,
    out_path: Path = GITCONFIG_PATH_DEFAULT,
) -> None:
    """Atomically write the curated gitconfig to `out_path`.

    `name` / `email` default to the host's `git config user.{name,email}`.
    Re-running picks up any host-side gitconfig edits since the last run
    (the installer calls this on every `claude-sandbox install` run).
    """
    user_name = name if name is not None else _git_config_get("user.name")
    user_email = email if email is not None else _git_config_get("user.email")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = render(user_name, user_email)
    _atomic_write(out_path, body, mode=0o644)


def render(user_name: str, user_email: str) -> str:
    """Render the curated gitconfig to a string.

    Pure function — same input always produces the same bytes. Tests
    assert against this directly.

    Sections:
      - [user] — host identity for commit attribution.
      - [credential "https://github.com"] / [credential "https://gitlab.diamond.ac.uk"]
        — `gh` / `glab` as the helpers so `git push` works inside the
        sandbox without an OAuth popup.
      - [url …] — ssh→https rewrites for github.com and
        gitlab.diamond.ac.uk so `git clone git@host:path` transparently
        uses the credential helpers above. Both scp form
        (`git@host:`) and URL form (`ssh://git@host/`) are listed
        because git's insteadOf is a fixed-string prefix match, not a
        parsed URL.
      - [init] — defaultBranch=main keeps `git init` reproducible.
      - [safe] — directory=* so git inside bwrap doesn't reject the
        bound workspace as "not safe" (UID-mismatch under bwrap's user
        namespace mapping is otherwise common).
    """
    return (
        "[user]\n"
        f"    name = {user_name}\n"
        f"    email = {user_email}\n"
        '[credential "https://github.com"]\n'
        "    helper = !gh auth git-credential\n"
        '[credential "https://gitlab.diamond.ac.uk"]\n'
        "    helper = !glab auth git-credential\n"
        '[url "https://github.com/"]\n'
        "    insteadOf = git@github.com:\n"
        "    insteadOf = ssh://git@github.com/\n"
        '[url "https://gitlab.diamond.ac.uk/"]\n'
        "    insteadOf = git@gitlab.diamond.ac.uk:\n"
        "    insteadOf = ssh://git@gitlab.diamond.ac.uk/\n"
        "[init]\n"
        "    defaultBranch = main\n"
        "[safe]\n"
        "    directory = *\n"
    )


def _git_config_get(key: str) -> str:
    """Return host `git config --get key`; empty string on miss."""
    result = subprocess.run(
        ["git", "config", "--get", key],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _atomic_write(path: Path, content: str, mode: int) -> None:
    """tmp + rename. A concurrent reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
