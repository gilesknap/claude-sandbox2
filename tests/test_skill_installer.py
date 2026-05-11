"""skill_installer: copy-if-missing, refuse-if-different, --force,
--all, --bundle, glob expansion, listing.

Tests build a fixture source tree at tmp_path/src/.claude/{skills,commands}/
so they don't depend on the repo's shipped payload (which is also
tested via the integration test in CI).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_sandbox import skill_installer
from claude_sandbox.skill_installer import (
    BundleNotFoundError,
    SkillInstallerError,
    SkillNotFoundError,
    install_commands,
    install_skills,
    list_commands,
    list_skills,
)

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


@pytest.fixture
def src_dir(tmp_path: Path) -> Path:
    """Build a fake `<src>/.claude/{skills,commands}/` tree.

    Skills (each with a SKILL.md and a sibling .md):
        diagnose, tdd, pocock-tdd, pocock-diagnose
    Commands:
        grill-me.md, memo.md, pocock-helper.md
    Plus a bundles.toml under src/claude_sandbox/data/.
    """
    src = tmp_path / "src"
    skills = src / ".claude" / "skills"
    cmds = src / ".claude" / "commands"
    skills.mkdir(parents=True)
    cmds.mkdir(parents=True)

    for name in ("diagnose", "tdd", "pocock-tdd", "pocock-diagnose"):
        d = skills / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill body.\n---\n")
        (d / "EXTRA.md").write_text(f"# extra notes for {name}\n")

    for name in ("grill-me", "memo", "pocock-helper"):
        (cmds / f"{name}.md").write_text(f"---\ndescription: {name} command body.\n---\n# {name}\n")

    # bundles.toml with a populated bundle and an empty bundle.
    bundles_dir = src / "src" / "claude_sandbox" / "data"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "bundles.toml").write_text(
        "[bundles.pocock]\n"
        'skills = ["pocock-tdd", "pocock-diagnose"]\n'
        'commands = ["pocock-helper"]\n'
        "\n"
        "[bundles.empty]\n"
        "skills = []\n"
        "commands = []\n"
    )
    return src


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_skills_returns_alphabetised_names(src_dir: Path) -> None:
    assert list_skills(src_dir) == ["diagnose", "pocock-diagnose", "pocock-tdd", "tdd"]


def test_list_commands_returns_alphabetised_names(src_dir: Path) -> None:
    assert list_commands(src_dir) == ["grill-me", "memo", "pocock-helper"]


def test_list_skills_empty_when_no_directory(tmp_path: Path) -> None:
    assert list_skills(tmp_path) == []
    assert list_commands(tmp_path) == []


# ---------------------------------------------------------------------------
# Skill install: copy / skip / refuse / force / all / bundle / glob
# ---------------------------------------------------------------------------


def test_install_skill_copy_if_missing(src_dir: Path, workspace: Path) -> None:
    result = install_skills(["diagnose"], src_dir, workspace)
    assert result.copied == ["diagnose"]
    assert (workspace / ".claude" / "skills" / "diagnose" / "SKILL.md").is_file()
    # The sibling .md is also copied (full directory tree).
    assert (workspace / ".claude" / "skills" / "diagnose" / "EXTRA.md").is_file()


def test_install_skill_skip_if_byte_identical(src_dir: Path, workspace: Path) -> None:
    install_skills(["diagnose"], src_dir, workspace)
    result = install_skills(["diagnose"], src_dir, workspace)
    assert result.copied == []
    assert result.skipped_identical == ["diagnose"]


def test_install_skill_refuse_if_different_content(src_dir: Path, workspace: Path) -> None:
    install_skills(["diagnose"], src_dir, workspace)
    # User edits the workspace copy.
    (workspace / ".claude" / "skills" / "diagnose" / "SKILL.md").write_text("user edit\n")
    result = install_skills(["diagnose"], src_dir, workspace)
    assert result.refused_different == ["diagnose"]
    assert result.copied == []
    # User's edit is preserved on refuse.
    assert (workspace / ".claude" / "skills" / "diagnose" / "SKILL.md").read_text() == "user edit\n"


def test_install_skill_force_overwrites(src_dir: Path, workspace: Path) -> None:
    install_skills(["diagnose"], src_dir, workspace)
    edited = workspace / ".claude" / "skills" / "diagnose" / "SKILL.md"
    edited.write_text("user edit\n")
    result = install_skills(["diagnose"], src_dir, workspace, force=True)
    assert result.forced == ["diagnose"]
    assert "diagnose skill body" in edited.read_text()


def test_install_skill_all(src_dir: Path, workspace: Path) -> None:
    result = install_skills([], src_dir, workspace, all=True)
    assert sorted(result.copied) == ["diagnose", "pocock-diagnose", "pocock-tdd", "tdd"]


def test_install_skill_bundle_resolves_via_bundles_toml(src_dir: Path, workspace: Path) -> None:
    result = install_skills([], src_dir, workspace, bundle="pocock")
    assert sorted(result.copied) == ["pocock-diagnose", "pocock-tdd"]


def test_install_skill_empty_bundle_succeeds_quietly(
    src_dir: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance: an empty bundle is success — log a notice, exit 0."""
    result = install_skills([], src_dir, workspace, bundle="empty")
    assert result.copied == []
    captured = capsys.readouterr()
    assert "no skill" in captured.err.lower()


def test_install_skill_unknown_bundle_raises(src_dir: Path, workspace: Path) -> None:
    with pytest.raises(BundleNotFoundError):
        install_skills([], src_dir, workspace, bundle="nope")


def test_install_skill_glob_expansion(src_dir: Path, workspace: Path) -> None:
    result = install_skills(["pocock-*"], src_dir, workspace)
    assert sorted(result.copied) == ["pocock-diagnose", "pocock-tdd"]


def test_install_skill_glob_no_match_raises(src_dir: Path, workspace: Path) -> None:
    with pytest.raises(SkillNotFoundError):
        install_skills(["nope-*"], src_dir, workspace)


def test_install_skill_unknown_name_raises(src_dir: Path, workspace: Path) -> None:
    with pytest.raises(SkillNotFoundError):
        install_skills(["does-not-exist"], src_dir, workspace)


def test_install_skill_no_args_raises(src_dir: Path, workspace: Path) -> None:
    """A bare `install-skill` without --all / --bundle / NAMES is an error."""
    with pytest.raises(SkillInstallerError):
        install_skills([], src_dir, workspace)


# ---------------------------------------------------------------------------
# Command install (mirrors skill, but the unit is a single .md file)
# ---------------------------------------------------------------------------


def test_install_command_copy_if_missing(src_dir: Path, workspace: Path) -> None:
    result = install_commands(["grill-me"], src_dir, workspace)
    assert result.copied == ["grill-me"]
    assert (workspace / ".claude" / "commands" / "grill-me.md").is_file()


def test_install_command_skip_if_byte_identical(src_dir: Path, workspace: Path) -> None:
    install_commands(["grill-me"], src_dir, workspace)
    result = install_commands(["grill-me"], src_dir, workspace)
    assert result.skipped_identical == ["grill-me"]


def test_install_command_refuse_if_different(src_dir: Path, workspace: Path) -> None:
    install_commands(["grill-me"], src_dir, workspace)
    (workspace / ".claude" / "commands" / "grill-me.md").write_text("user edit\n")
    result = install_commands(["grill-me"], src_dir, workspace)
    assert result.refused_different == ["grill-me"]


def test_install_command_all(src_dir: Path, workspace: Path) -> None:
    result = install_commands([], src_dir, workspace, all=True)
    assert sorted(result.copied) == ["grill-me", "memo", "pocock-helper"]


def test_install_command_bundle(src_dir: Path, workspace: Path) -> None:
    result = install_commands([], src_dir, workspace, bundle="pocock")
    assert result.copied == ["pocock-helper"]


def test_install_command_glob(src_dir: Path, workspace: Path) -> None:
    result = install_commands(["pocock-*"], src_dir, workspace)
    assert result.copied == ["pocock-helper"]


# ---------------------------------------------------------------------------
# Print summary smoke
# ---------------------------------------------------------------------------


def test_print_summary_emits_lines(
    src_dir: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = install_skills(["diagnose"], src_dir, workspace)
    result.print_summary("skill")
    captured = capsys.readouterr()
    assert "copied skill diagnose" in captured.out


# ---------------------------------------------------------------------------
# Sanity: shipped repo payload is also valid
# ---------------------------------------------------------------------------


def test_shipped_payload_lists_expected_skills(repo_root: Path) -> None:
    """The shipped `.claude/` must contain the 5 PRD skills."""
    skills = skill_installer.list_skills(repo_root)
    expected = (
        "diagnose",
        "tdd",
        "grill-with-docs",
        "improve-codebase-architecture",
        "triage",
    )
    for required in expected:
        assert required in skills, f"missing shipped skill: {required}"


def test_shipped_payload_lists_expected_commands(repo_root: Path) -> None:
    cmds = skill_installer.list_commands(repo_root)
    for required in (
        "grill-me",
        "memo",
        "write-a-skill",
        "zoom-out",
        "to-prd",
        "to-issues",
        "toolbox",
        "toolbox-update",
        "verify-sandbox",
    ):
        assert required in cmds, f"missing shipped command: {required}"
