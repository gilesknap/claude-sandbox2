"""Opt-in copier for shipped skills (directory trees) and commands (flat .md).

Both `claude-sandbox install-skill` and `install-command` route through
here. The shape is:

  * Source = the cloned repo's `.claude/{skills,commands}/`.
  * Destination = `<workspace>/.claude/{skills,commands}/` by default.
  * Per-file rules: copy if missing, no-op if byte-identical, refuse on
    different content unless `--force`.

Why a single module for skills + commands: the operations are
isomorphic — only the unit (directory vs file) and the source list
(directory listing vs `*.md` listing) differ. Sharing the dispatcher
also means `--all`, `--bundle`, and glob expansion are implemented
once.

The prior bash project's `_artifact_copy_shipped_skills` /
`_artifact_copy_shipped_commands` were ~250 LoC of duplication; this
trades that for one InstallResult dataclass and two thin shims.
"""

from __future__ import annotations

import filecmp
import fnmatch
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class SkillInstallerError(RuntimeError):
    """Base for typed failure paths the CLI maps to non-zero exits."""


class SkillNotFoundError(SkillInstallerError):
    """Requested name (or glob with no matches) does not exist in source."""


class BundleNotFoundError(SkillInstallerError):
    """`--bundle NAME` references a key that does not exist in bundles.toml."""


class SkillConflictError(SkillInstallerError):
    """Destination exists with different content and `--force` was not given."""


@dataclass
class InstallResult:
    """What an install_* invocation did. Tests assert on the lists."""

    copied: list[str] = field(default_factory=list)
    skipped_identical: list[str] = field(default_factory=list)
    refused_different: list[str] = field(default_factory=list)
    forced: list[str] = field(default_factory=list)

    def print_summary(self, kind: str) -> None:
        """Human-readable summary for the CLI. `kind` is "skill" or "command"."""
        for name in self.copied:
            print(f"  copied {kind} {name}")
        for name in self.forced:
            print(f"  overwrote {kind} {name} (--force)")
        for name in self.skipped_identical:
            print(f"  skipped {kind} {name} (already up to date)")
        for name in self.refused_different:
            print(
                f"  refused {kind} {name} — workspace copy differs. Re-run with --force "
                f"to overwrite, or reconcile by hand.",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Public API: install_skills / install_commands
# ---------------------------------------------------------------------------


def install_skills(
    names: list[str],
    src_dir: Path,
    workspace: Path,
    *,
    force: bool = False,
    all: bool = False,  # noqa: A002 (mirrors the CLI flag name)
    bundle: str | None = None,
) -> InstallResult:
    """Install one or more shipped skills into `<workspace>/.claude/skills/`."""
    return _install(
        names,
        src_dir,
        workspace,
        force=force,
        all_=all,
        bundle=bundle,
        kind="skill",
    )


def install_commands(
    names: list[str],
    src_dir: Path,
    workspace: Path,
    *,
    force: bool = False,
    all: bool = False,  # noqa: A002
    bundle: str | None = None,
) -> InstallResult:
    """Install one or more shipped commands into `<workspace>/.claude/commands/`."""
    return _install(
        names,
        src_dir,
        workspace,
        force=force,
        all_=all,
        bundle=bundle,
        kind="command",
    )


# ---------------------------------------------------------------------------
# Listing helpers (also used by `claude-sandbox list-{skills,commands}`)
# ---------------------------------------------------------------------------


def list_skills(src_dir: Path) -> list[str]:
    """Sorted skill names found in `<src>/.claude/skills/`.

    A "skill" is a directory containing a SKILL.md. Anything else under
    `.claude/skills/` (stray .md, dotfiles) is ignored.
    """
    root = src_dir / ".claude" / "skills"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def list_commands(src_dir: Path) -> list[str]:
    """Sorted command names (`*.md` basename without extension) under `<src>/.claude/commands/`."""
    root = src_dir / ".claude" / "commands"
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.iterdir() if p.is_file() and p.suffix == ".md")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _install(
    names: list[str],
    src_dir: Path,
    workspace: Path,
    *,
    force: bool,
    all_: bool,
    bundle: str | None,
    kind: str,
) -> InstallResult:
    available = list_skills(src_dir) if kind == "skill" else list_commands(src_dir)
    src_root = src_dir / ".claude" / ("skills" if kind == "skill" else "commands")
    dst_root = workspace / ".claude" / ("skills" if kind == "skill" else "commands")

    resolved = _resolve_names(names, available, src_dir, all_=all_, bundle=bundle, kind=kind)

    result = InstallResult()
    if not resolved:
        # Empty bundle / explicit empty selection is success per acceptance
        # criteria — log to stderr so the user sees what happened, exit 0.
        if bundle is not None:
            print(
                f"claude-sandbox: bundle '{bundle}' has no {kind}s to install.",
                file=sys.stderr,
            )
        return result

    dst_root.mkdir(parents=True, exist_ok=True)
    for name in resolved:
        if kind == "skill":
            _install_one_skill(name, src_root, dst_root, force=force, result=result)
        else:
            _install_one_command(name, src_root, dst_root, force=force, result=result)

    return result


def _resolve_names(
    names: list[str],
    available: list[str],
    src_dir: Path,
    *,
    all_: bool,
    bundle: str | None,
    kind: str,
) -> list[str]:
    """Turn the (names, --all, --bundle) flags into a deduped, ordered list.

    Order: --all > --bundle > positional names. Globs in positional
    names expand against `available`. Empty results from --bundle are
    not an error (acceptance criterion); a glob that matches nothing
    raises SkillNotFoundError so the user notices typos.
    """
    if all_:
        return list(available)

    if bundle is not None:
        bundle_entries = _read_bundle(src_dir, bundle, kind)
        # Bundle entries are not glob-expanded; they're explicit lists.
        # Validate each entry exists in source so a stale bundle is loud.
        unknown = [n for n in bundle_entries if n not in available]
        if unknown:
            raise SkillNotFoundError(
                f"bundle '{bundle}' lists unknown {kind}(s): {', '.join(unknown)}. "
                f"Available: {', '.join(available) or '<none>'}"
            )
        return bundle_entries

    if not names:
        raise SkillInstallerError(f"no {kind}s requested. Pass NAME(s), --all, or --bundle NAME.")

    resolved: list[str] = []
    seen: set[str] = set()
    for pattern in names:
        if any(c in pattern for c in "*?["):
            matches = sorted(fnmatch.filter(available, pattern))
            if not matches:
                raise SkillNotFoundError(
                    f"glob '{pattern}' matched no {kind}s. "
                    f"Available: {', '.join(available) or '<none>'}"
                )
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    resolved.append(m)
        else:
            if pattern not in available:
                raise SkillNotFoundError(
                    f"{kind} '{pattern}' not found in source. "
                    f"Available: {', '.join(available) or '<none>'}"
                )
            if pattern not in seen:
                seen.add(pattern)
                resolved.append(pattern)
    return resolved


def _read_bundle(src_dir: Path, bundle: str, kind: str) -> list[str]:
    """Read `[bundles.<bundle>]` from `<src>/src/claude_sandbox/data/bundles.toml`.

    Schema:
        [bundles.<NAME>]
        skills = ["a", "b"]
        commands = ["x", "y"]

    Both keys optional. Missing keys default to []. The bundle key
    itself MUST exist — typos shouldn't silently install nothing.
    """
    bundles_path = src_dir / "src" / "claude_sandbox" / "data" / "bundles.toml"
    if not bundles_path.is_file():
        # Fall back to the package-data location (when src_dir doesn't
        # mirror the repo layout, e.g. an editable install).
        try:
            import importlib.resources

            bundles_text = (
                importlib.resources.files("claude_sandbox.data")
                .joinpath("bundles.toml")
                .read_text()
            )
        except (FileNotFoundError, ModuleNotFoundError) as exc:
            raise BundleNotFoundError(
                f"bundles.toml not found under {src_dir}; cannot resolve --bundle {bundle}."
            ) from exc
        data = tomllib.loads(bundles_text)
    else:
        with bundles_path.open("rb") as f:
            data = tomllib.load(f)

    bundles = data.get("bundles") or {}
    if bundle not in bundles:
        available = ", ".join(sorted(bundles.keys())) or "<none>"
        raise BundleNotFoundError(
            f"bundle '{bundle}' not defined in bundles.toml. Available bundles: {available}"
        )
    entry = bundles[bundle] or {}
    plural = "skills" if kind == "skill" else "commands"
    return list(entry.get(plural) or [])


def _install_one_skill(
    name: str,
    src_root: Path,
    dst_root: Path,
    *,
    force: bool,
    result: InstallResult,
) -> None:
    """Copy `<src>/.claude/skills/<name>/` -> `<dst>/.claude/skills/<name>/`.

    Per-file rules:
      - copy if missing,
      - no-op if every file matches byte-for-byte,
      - refuse if any file differs (unless --force).

    The whole-tree comparison uses filecmp.dircmp shallow=False so byte
    equality is real (not stat-only).
    """
    src = src_root / name
    dst = dst_root / name
    if not src.is_dir():
        raise SkillNotFoundError(f"skill source {src} not found.")

    if not dst.exists():
        shutil.copytree(src, dst)
        result.copied.append(name)
        return

    if _trees_byte_equal(src, dst):
        result.skipped_identical.append(name)
        return

    if force:
        shutil.rmtree(dst)
        shutil.copytree(src, dst)
        result.forced.append(name)
        return

    result.refused_different.append(name)


def _install_one_command(
    name: str,
    src_root: Path,
    dst_root: Path,
    *,
    force: bool,
    result: InstallResult,
) -> None:
    """Copy `<src>/.claude/commands/<name>.md` -> `<dst>/.claude/commands/<name>.md`."""
    src = src_root / f"{name}.md"
    dst = dst_root / f"{name}.md"
    if not src.is_file():
        raise SkillNotFoundError(f"command source {src} not found.")

    if not dst.exists():
        shutil.copy2(src, dst)
        result.copied.append(name)
        return

    if dst.read_bytes() == src.read_bytes():
        result.skipped_identical.append(name)
        return

    if force:
        shutil.copy2(src, dst)
        result.forced.append(name)
        return

    result.refused_different.append(name)


def _trees_byte_equal(a: Path, b: Path) -> bool:
    """True iff every file under a/ has a byte-identical sibling under b/."""
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    # filecmp.dircmp.diff_files is stat-based; re-confirm with a real
    # byte compare so mtime drift doesn't trick us.
    matches, mismatches, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    if mismatches or errors:
        return False
    return all(_trees_byte_equal(a / sub, b / sub) for sub in cmp.common_dirs)
