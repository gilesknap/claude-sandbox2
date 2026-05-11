"""typer entry point. Maps subcommands to module functions and converts
typed exceptions to non-zero exit + stderr.

The 6 commands:

  install            container artifacts + workspace wiring
  upgrade            git pull + uv sync + re-exec install
  list-skills        enumerate <src>/.claude/skills/
  list-commands      enumerate <src>/.claude/commands/
  install-skill      copy named skills into <workspace>/.claude/skills/
  install-command    copy named commands into <workspace>/.claude/commands/

Verification is the slash command `/verify-sandbox` (consumed by Claude
itself, spec at `.claude/commands/verify-sandbox.md`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from claude_sandbox import installer, probe, skill_installer

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="bwrap-isolated Claude Code installer for Debian/Ubuntu devcontainers.",
)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


@app.command("install")
def install_cmd(
    workspace: Path = typer.Option(  # noqa: B008
        None,
        "--workspace",
        "-w",
        help="Workspace to wire (.claude/settings.json + hook). Defaults to $PWD.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what would be placed; touch nothing on disk.",
    ),
) -> None:
    """Install the sandbox: container artifacts + workspace wiring. Idempotent."""
    try:
        result = installer.install(workspace=workspace, dry_run=dry_run)
    except (
        probe.UnsupportedHostError,
        probe.UserNamespacesBlockedError,
        installer.SettingsConflictError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(code=1) from exc

    if dry_run and result is not None:
        print("claude-sandbox install --dry-run:")
        for warning in result.warnings:
            print(f"  warning: {warning}")
        print("  container files:")
        for path, label in result.container_files:
            print(f"    {path} <- {label}")
        print("  workspace files:")
        for path, label in result.workspace_files:
            print(f"    {path} <- {label}")


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


@app.command("upgrade")
def upgrade_cmd(
    src_dir: Path = typer.Option(  # noqa: B008
        None,
        "--src",
        envvar="CLAUDE_SANDBOX_SRC_DIR",
        help="Source clone directory (default /opt/claude-sandbox-src).",
    ),
) -> None:
    """git pull the source clone, re-sync deps, then re-exec `install`.

    Refuses cleanly if the source clone is missing — the user should
    re-run the curl-bash installer in that case.
    """
    src = src_dir or Path("/opt/claude-sandbox-src")
    if not (src / ".git").is_dir():
        print(
            f"claude-sandbox: refusing — no git clone found at {src}. "
            f"Re-run the curl-bash installer to bootstrap.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    # Run sequentially so a failed pull / sync doesn't trigger an
    # `install` against a half-updated tree.
    for cmd in (
        ["git", "-C", str(src), "pull", "--ff-only"],
        ["uv", "sync", "--project", str(src)],
    ):
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(
                f"claude-sandbox: upgrade aborted — `{' '.join(cmd)}` exited "
                f"{result.returncode}.",
                file=sys.stderr,
            )
            raise typer.Exit(code=result.returncode)

    # exec to inherit stdout/stderr cleanly and avoid a double-Python boot.
    os.execvp("uv", ["uv", "run", "--project", str(src), "claude-sandbox", "install"])


# ---------------------------------------------------------------------------
# list-skills / list-commands
# ---------------------------------------------------------------------------


@app.command("list-skills")
def list_skills_cmd(
    src_dir: Path = typer.Option(  # noqa: B008
        None,
        "--src",
        envvar="CLAUDE_SANDBOX_SRC_DIR",
        help=(
            "Source dir to enumerate (default: locate via $PWD or "
            "/opt/claude-sandbox-src)."
        ),
    ),
) -> None:
    """Print one shipped skill name per line, alphabetised."""
    src = _resolve_src_dir(src_dir)
    for name in skill_installer.list_skills(src):
        print(name)


@app.command("list-commands")
def list_commands_cmd(
    src_dir: Path = typer.Option(  # noqa: B008
        None,
        "--src",
        envvar="CLAUDE_SANDBOX_SRC_DIR",
        help=(
            "Source dir to enumerate (default: locate via $PWD or "
            "/opt/claude-sandbox-src)."
        ),
    ),
) -> None:
    """Print one shipped command name per line, alphabetised."""
    src = _resolve_src_dir(src_dir)
    for name in skill_installer.list_commands(src):
        print(name)


# ---------------------------------------------------------------------------
# install-skill / install-command
# ---------------------------------------------------------------------------


@app.command("install-skill")
def install_skill_cmd(
    names: list[str] = typer.Argument(  # noqa: B008
        None,
        help="Skill name(s). Globs allowed (e.g. 'pocock-*').",
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite differing workspace copies."
    ),
    all_: bool = typer.Option(False, "--all", help="Install every shipped skill."),
    bundle: str = typer.Option(
        None, "--bundle", help="Install the named bundle from bundles.toml."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        None,
        "--workspace",
        "-w",
        help="Destination workspace root (default: $PWD).",
    ),
    src_dir: Path = typer.Option(  # noqa: B008
        None,
        "--src",
        envvar="CLAUDE_SANDBOX_SRC_DIR",
        help="Source dir (default: locate via $PWD or /opt/claude-sandbox-src).",
    ),
) -> None:
    """Copy shipped skills into <workspace>/.claude/skills/."""
    _run_install(
        skill_installer.install_skills,
        names or [],
        src_dir,
        workspace,
        force=force,
        all_=all_,
        bundle=bundle,
        kind="skill",
    )


@app.command("install-command")
def install_command_cmd(
    names: list[str] = typer.Argument(  # noqa: B008
        None,
        help="Command name(s) (no .md suffix). Globs allowed.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite differing workspace copies."
    ),
    all_: bool = typer.Option(False, "--all", help="Install every shipped command."),
    bundle: str = typer.Option(
        None, "--bundle", help="Install the named bundle from bundles.toml."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        None,
        "--workspace",
        "-w",
        help="Destination workspace root (default: $PWD).",
    ),
    src_dir: Path = typer.Option(  # noqa: B008
        None,
        "--src",
        envvar="CLAUDE_SANDBOX_SRC_DIR",
        help="Source dir (default: locate via $PWD or /opt/claude-sandbox-src).",
    ),
) -> None:
    """Copy shipped commands into <workspace>/.claude/commands/."""
    _run_install(
        skill_installer.install_commands,
        names or [],
        src_dir,
        workspace,
        force=force,
        all_=all_,
        bundle=bundle,
        kind="command",
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _run_install(
    fn,
    names: list[str],
    src_dir: Path | None,
    workspace: Path | None,
    *,
    force: bool,
    all_: bool,
    bundle: str | None,
    kind: str,
) -> None:
    src = _resolve_src_dir(src_dir)
    ws = workspace or Path.cwd()
    try:
        result = fn(names, src, ws, force=force, all=all_, bundle=bundle)
    except skill_installer.SkillInstallerError as exc:
        print(f"claude-sandbox: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc

    result.print_summary(kind)
    if result.refused_different and not force:
        raise typer.Exit(code=1)


def _resolve_src_dir(explicit: Path | None) -> Path:
    """Find the source clone. Order: explicit arg, env, /opt, walk-up.

    The walk-up path makes tests and local development work — running
    `claude-sandbox list-skills` in a checkout finds the local
    `.claude/` without needing /opt/claude-sandbox-src.
    """
    if explicit is not None:
        return explicit
    env = os.environ.get("CLAUDE_SANDBOX_SRC_DIR")
    if env:
        return Path(env)
    canonical = Path("/opt/claude-sandbox-src")
    if (canonical / ".claude").is_dir():
        return canonical
    # Walk up from $PWD looking for a `.claude/` directory next to a
    # pyproject.toml — this is "we are in the dev checkout" mode.
    here = Path.cwd().resolve()
    for parent in (here, *here.parents):
        if (parent / ".claude").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    # Final fallback: walk up from this file (e.g. when invoked from
    # the installed venv via the shim).
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".claude").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return canonical


if __name__ == "__main__":
    app()
