"""typer entry point. Maps subcommands to module functions and converts
typed exceptions to non-zero exit + stderr.

Slice 1 ships only `install` and `verify`; the other 5 commands
(`upgrade`, `list-skills`, `list-commands`, `install-skill`,
`install-command`) land in slice 2.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from claude_sandbox import installer, probe, verifier

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="bwrap-isolated Claude Code installer for Debian/Ubuntu devcontainers.",
)


@app.command("install")
def install_cmd(
    workspace: Path = typer.Option(  # noqa: B008
        None,
        "--workspace",
        "-w",
        help="Workspace to wire (.claude/settings.json + hook). Defaults to $PWD.",
    ),
) -> None:
    """Install the sandbox: container artifacts + workspace wiring. Idempotent."""
    try:
        installer.install(workspace=workspace)
    except (
        probe.UnsupportedHostError,
        probe.UserNamespacesBlockedError,
        installer.SettingsConflictError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(code=1) from exc


@app.command("verify")
def verify_cmd(
    workspace: Path = typer.Option(  # noqa: B008
        None,
        "--workspace",
        "-w",
        help="Workspace whose .claude/commands/verify-sandbox.md to run.",
    ),
) -> None:
    """Run the 18 sandbox checks against the live process."""
    code = verifier.run(workspace=workspace)
    raise typer.Exit(code=code)


if __name__ == "__main__":
    app()
