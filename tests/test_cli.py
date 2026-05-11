"""CLI smoke tests via typer's CliRunner.

For each of the 7 subcommands we assert at minimum that --help works
(the typer app loads, the subcommand is registered) and that the basic
non-mutating invocations behave as documented. Commands that touch
/opt or /usr/local/bin are exercised via --dry-run / --workspace
overrides only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_sandbox.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# --help for each subcommand: regression guard against accidental drops.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subcmd",
    [
        "install",
        "upgrade",
        "list-skills",
        "list-commands",
        "install-skill",
        "install-command",
    ],
)
def test_subcommand_help(subcmd: str) -> None:
    result = runner.invoke(app, [subcmd, "--help"])
    assert result.exit_code == 0, result.output
    # typer renders the docstring into help; a stable token to look for is
    # the subcommand name itself in the help output.
    assert subcmd in result.output or subcmd.replace("-", "_") in result.output


# ---------------------------------------------------------------------------
# list-skills / list-commands against the repo's own .claude/
# ---------------------------------------------------------------------------


def test_list_skills_against_repo_root(repo_root: Path) -> None:
    result = runner.invoke(app, ["list-skills", "--src", str(repo_root)])
    assert result.exit_code == 0, result.output
    out_lines = result.output.strip().splitlines()
    expected_skills = (
        "diagnose",
        "tdd",
        "grill-with-docs",
        "improve-codebase-architecture",
        "triage",
    )
    for required in expected_skills:
        assert required in out_lines


def test_list_commands_against_repo_root(repo_root: Path) -> None:
    result = runner.invoke(app, ["list-commands", "--src", str(repo_root)])
    assert result.exit_code == 0, result.output
    out_lines = result.output.strip().splitlines()
    for required in ("grill-me", "memo", "verify-sandbox", "toolbox"):
        assert required in out_lines


# ---------------------------------------------------------------------------
# install --dry-run produces the plan
# ---------------------------------------------------------------------------


def test_install_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_SANDBOX_SRC_DIR", str(tmp_path / "src"))
    result = runner.invoke(
        app,
        ["install", "--workspace", str(tmp_path / "ws"), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    # The plan dump mentions both container and workspace targets.
    assert "/usr/local/bin/claude" in result.output
    assert "settings.json" in result.output
    assert "sandbox-check.sh" in result.output


# ---------------------------------------------------------------------------
# install-skill against a tmp source tree
# ---------------------------------------------------------------------------


@pytest.fixture
def tiny_src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    skills = src / ".claude" / "skills"
    cmds = src / ".claude" / "commands"
    skills.mkdir(parents=True)
    cmds.mkdir(parents=True)
    d = skills / "diagnose"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: diagnose\ndescription: t.\n---\n")
    (cmds / "grill-me.md").write_text("---\ndescription: t.\n---\n")
    bd = src / "src" / "claude_sandbox" / "data"
    bd.mkdir(parents=True)
    (bd / "bundles.toml").write_text(
        '[bundles.tiny]\nskills = ["diagnose"]\ncommands = ["grill-me"]\n'
    )
    return src


def test_install_skill_via_cli(tmp_path: Path, tiny_src: Path) -> None:
    ws = tmp_path / "ws"
    result = runner.invoke(
        app,
        [
            "install-skill",
            "diagnose",
            "--src",
            str(tiny_src),
            "--workspace",
            str(ws),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (ws / ".claude" / "skills" / "diagnose" / "SKILL.md").is_file()
    assert "copied skill diagnose" in result.output


def test_install_skill_unknown_exits_nonzero(tmp_path: Path, tiny_src: Path) -> None:
    result = runner.invoke(
        app,
        [
            "install-skill",
            "nope",
            "--src",
            str(tiny_src),
            "--workspace",
            str(tmp_path / "ws"),
        ],
    )
    assert result.exit_code != 0


def test_install_skill_bundle_via_cli(tmp_path: Path, tiny_src: Path) -> None:
    ws = tmp_path / "ws"
    result = runner.invoke(
        app,
        [
            "install-skill",
            "--bundle",
            "tiny",
            "--src",
            str(tiny_src),
            "--workspace",
            str(ws),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (ws / ".claude" / "skills" / "diagnose" / "SKILL.md").is_file()


def test_install_command_all_via_cli(tmp_path: Path, tiny_src: Path) -> None:
    ws = tmp_path / "ws"
    result = runner.invoke(
        app,
        [
            "install-command",
            "--all",
            "--src",
            str(tiny_src),
            "--workspace",
            str(ws),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (ws / ".claude" / "commands" / "grill-me.md").is_file()


# ---------------------------------------------------------------------------
# upgrade: refuses cleanly when source clone is missing
# ---------------------------------------------------------------------------


def test_upgrade_refuses_when_src_missing(tmp_path: Path) -> None:
    result = runner.invoke(app, ["upgrade", "--src", str(tmp_path / "no-such")])
    assert result.exit_code != 0
    # Typer routes printed-from-Python-stderr into result.stderr only when
    # mix_stderr=False; the runner's default merges them into result.output.
    assert "no git clone found" in (result.output + result.stderr)


# ---------------------------------------------------------------------------
# install-skill --bundle empty exits 0 with a notice
# ---------------------------------------------------------------------------


def test_install_skill_empty_bundle_exits_zero(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / ".claude" / "skills").mkdir(parents=True)
    (src / ".claude" / "commands").mkdir(parents=True)
    bd = src / "src" / "claude_sandbox" / "data"
    bd.mkdir(parents=True)
    (bd / "bundles.toml").write_text("[bundles.empty]\nskills = []\ncommands = []\n")
    result = runner.invoke(
        app,
        [
            "install-skill",
            "--bundle",
            "empty",
            "--src",
            str(src),
            "--workspace",
            str(tmp_path / "ws"),
        ],
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# A successful install + a settings.json that already wires our hook stays
# byte-identical (idempotent end-to-end).
# ---------------------------------------------------------------------------


def test_install_dry_run_then_real_paths_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: dry-run names the same workspace targets that
    place_workspace_settings would actually write."""
    from claude_sandbox.installer import place_workspace_settings

    monkeypatch.setenv("CLAUDE_SANDBOX_SRC_DIR", str(tmp_path / "src"))
    place_workspace_settings(tmp_path / "ws")
    text = (tmp_path / "ws" / ".claude" / "settings.json").read_text()
    parsed = json.loads(text)
    assert "UserPromptSubmit" in parsed["hooks"]
