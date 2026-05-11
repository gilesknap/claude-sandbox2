"""Install orchestrator. Probes -> places container artifacts -> places
workspace artifacts. Idempotent — re-runs after a devcontainer rebuild
re-establish container state without disturbing workspace edits.

Slice 2 graduated the inline gitconfig generator and settings merger
into standalone modules (`gitconfig.py`, `settings_merger.py`); this
file re-exports `SettingsConflictError` for backward compatibility
with slice 1's import surface.
"""

from __future__ import annotations

import contextlib
import importlib.resources
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from claude_sandbox import gitconfig, probe, settings_merger
from claude_sandbox.settings_merger import SettingsConflictError

# Container-mode v1 paths. v2 introduces an install_paths
# parameterisation that hosts non-root + host-mode installs.
SHADOW_CLAUDE_PATH = Path("/usr/local/bin/claude")
SHADOW_CLI_PATH = Path("/usr/local/bin/claude-sandbox")
REAL_CLAUDE_PATH = Path("/opt/claude/bin/claude")
GITCONFIG_PATH = Path("/etc/claude-gitconfig")
SRC_DIR_DEFAULT = Path("/opt/claude-sandbox-src")

OUR_HOOK_BLOCK = {
    "hooks": [
        {
            "type": "command",
            "command": ".claude/hooks/sandbox-check.sh",
        }
    ]
}


@dataclass
class DryRunPlan:
    """What an install run *would* place, without touching disk.

    The orchestrator records each placement decision into one of these
    so tests can assert on the plan without needing a writable /opt or
    /usr/local/bin. Each list holds (target_path, source_or_blob_label).
    """

    container_files: list[tuple[Path, str]] = field(default_factory=list)
    workspace_files: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def install(workspace: Path | None = None, *, dry_run: bool = False) -> DryRunPlan | None:
    """Run the full install orchestration. Raises on refusal-paths.

    `workspace` defaults to $PWD; tests pass a tmpdir.
    `dry_run=True` records intended placements into a DryRunPlan and
    skips every state mutation (probe, file write, subprocess) so the
    orchestrator can be exercised on any host.
    """
    if workspace is None:
        workspace = Path.cwd()
    src_dir = Path(os.environ.get("CLAUDE_SANDBOX_SRC_DIR") or SRC_DIR_DEFAULT)

    if dry_run:
        return _plan(workspace, src_dir)

    # 1. Probe before any state mutation. apt+userns first, bwrap last
    # (bwrap may not be installed yet on a fresh devcontainer).
    probe.apt_or_refuse()
    probe.kernel_userns_or_refuse()

    # 2. Mount-scan is informational — emit warnings but never refuse.
    for warning in probe.mount_scan():
        print(warning, file=sys.stderr)

    # 3. The bash `install` wrapper handles apt + curl-installs; by the
    # time we re-exec into uv-run we expect bwrap on the host. Probe
    # again to confirm.
    probe.bwrap_or_refuse()

    # 4. Container-scoped artifacts. Order: real claude move first
    # (we need to know where it is to bake the path into the shadow),
    # then shadow + cli + gitconfig.
    place_real_claude()
    place_shadow(src_dir)
    place_cli_shim(src_dir)
    write_gitconfig()

    # 5. Workspace-scoped artifacts. Idempotent: re-runs are no-ops on a
    # workspace already fully wired.
    place_workspace_settings(workspace)
    place_workspace_hook(workspace, src_dir)

    print(
        f"claude-sandbox: install complete in {workspace}\n"
        f"  - run 'claude' (shadow at {SHADOW_CLAUDE_PATH}) to start a sandboxed session\n"
        f"  - run '/verify-sandbox' inside Claude (or 'claude-sandbox verify') to "
        f"confirm the 18-check battery"
    )
    return None


def _plan(workspace: Path, src_dir: Path) -> DryRunPlan:
    """Produce a DryRunPlan describing the full install without mutating state."""
    plan = DryRunPlan()

    # Mount-scan warnings are pure-read; safe to call.
    plan.warnings.extend(probe.mount_scan())

    plan.container_files.append((SHADOW_CLAUDE_PATH, "claude-shadow (rendered)"))
    plan.container_files.append((SHADOW_CLI_PATH, "claude-sandbox CLI shim"))
    plan.container_files.append((REAL_CLAUDE_PATH, "real claude (moved from ~/.local/bin)"))
    plan.container_files.append((GITCONFIG_PATH, "curated gitconfig"))

    plan.workspace_files.append((workspace / ".claude" / "settings.json", "settings.json (merged)"))
    hook_dst = workspace / ".claude" / "hooks" / "sandbox-check.sh"
    plan.workspace_files.append((hook_dst, f"{src_dir}/.claude/hooks/sandbox-check.sh"))
    return plan


# Sentinel substrings every shadow contains (the recursion-guard env
# var and the sourced bwrap-argv builder). A real claude binary contains
# neither, so finding either in the first 4 KB is a reliable "this is a
# shadow, not the real thing" signal.
_SHADOW_SENTINELS = (b"IS_SANDBOX", b"bwrap_argv_build")


def _looks_like_shadow(path: Path) -> bool:
    """True if `path` is a bash-script shadow rather than the real Claude binary.

    The real claude is a multi-hundred-MB compiled binary; the shadow is
    a small bash script. A shebang + sentinel-substring check on the
    first 4 KB distinguishes them cheaply.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return False
    return any(sentinel in head for sentinel in _SHADOW_SENTINELS)


def _resolve_real_claude_source() -> Path | None:
    """Locate the real Claude binary Anthropic's installer dropped.

    Tries ~/.local/bin/claude (resolving through the symlink Anthropic
    now uses to point at the versioned binary under ~/.local/share),
    then falls back to `shutil.which("claude")` — but skips any
    candidate that looks like our shadow, so a shadow already on $PATH
    can never be copied into REAL_CLAUDE_PATH.
    """
    home_local = Path.home() / ".local" / "bin" / "claude"
    if home_local.exists():
        resolved = home_local.resolve()
        if resolved.is_file() and not _looks_like_shadow(resolved):
            return resolved
    which = shutil.which("claude")
    if which:
        candidate = Path(which).resolve()
        if candidate.is_file() and not _looks_like_shadow(candidate):
            return candidate
    return None


def place_real_claude() -> None:
    """Place the real Claude binary at /opt/claude/bin/claude.

    The shadow at /usr/local/bin/claude must win on $PATH; parking the
    real binary under /opt guarantees that even users with
    ~/.local/bin on PATH hit the shadow first.

    Idempotent. Replaces the target if it's actually a shadow (a prior
    install that ran with a shadow already on $PATH could end up with
    the shadow copied into REAL_CLAUDE_PATH; the shadow then execs
    itself forever, hanging every launch).
    """
    REAL_CLAUDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if REAL_CLAUDE_PATH.is_file() and not _looks_like_shadow(REAL_CLAUDE_PATH):
        return

    source = _resolve_real_claude_source()
    if source is None:
        return

    # Atomic via tmp + replace — never leave a half-written 200+ MB
    # binary at REAL_CLAUDE_PATH if the copy is interrupted.
    tmp = REAL_CLAUDE_PATH.with_name(REAL_CLAUDE_PATH.name + ".tmp")
    shutil.copy2(str(source), str(tmp))
    tmp.chmod(0o755)
    os.replace(str(tmp), str(REAL_CLAUDE_PATH))


def place_shadow(src_dir: Path) -> None:
    """Substitute the shadow template's @@PLACEHOLDERS@@ and write atomically.

    Atomic via tmp + rename — a concurrent reader never sees a half-
    written binary.
    """
    template = _read_data_file("claude-shadow")
    rendered = (
        template.replace("@@REAL_CLAUDE@@", str(REAL_CLAUDE_PATH))
        .replace("@@SRC_DIR@@", str(src_dir))
        .replace("@@GITCONFIG_PATH@@", str(GITCONFIG_PATH))
    )
    _atomic_write(SHADOW_CLAUDE_PATH, rendered, mode=0o755)


def place_cli_shim(src_dir: Path) -> None:
    """Write /usr/local/bin/claude-sandbox: a thin shim that re-execs
    `uv run --project /opt/claude-sandbox-src claude-sandbox`.

    Distinct from the prior project's symlink-to-shadow trick: the CLI
    shim is its own thing; only the `claude` binary is shadowed.
    """
    shim = (
        "#!/usr/bin/env bash\n"
        "# claude-sandbox CLI shim. Re-execs into the project venv.\n"
        f'exec uv run --project {src_dir} claude-sandbox "$@"\n'
    )
    _atomic_write(SHADOW_CLI_PATH, shim, mode=0o755)


def write_gitconfig(name_override: str | None = None, email_override: str | None = None) -> None:
    """Generate /etc/claude-gitconfig from the host's user.name/user.email.

    Delegates to `gitconfig.generate` so the generation logic has one
    home (and one test target). Re-running picks up host gitconfig
    edits since the last invocation.
    """
    gitconfig.generate(
        name=name_override,
        email=email_override,
        out_path=GITCONFIG_PATH,
    )


def place_workspace_settings(workspace: Path) -> None:
    """Wire the sandbox-check hook into <workspace>/.claude/settings.json.

    Clean install when missing; one-key surgical merge of
    `hooks.UserPromptSubmit` when present. Refuses on conflict (a
    different hook command pointing at the same script). Never
    touches `permissions`, `env`, or any other key.
    """
    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    merged = settings_merger.merge_file(settings_path, OUR_HOOK_BLOCK)
    rendered = json.dumps(merged, indent=2) + "\n"
    _atomic_write(settings_path, rendered, mode=0o644)


def place_workspace_hook(workspace: Path, src_dir: Path) -> None:
    """Copy .claude/hooks/sandbox-check.sh into the workspace.

    Copy-if-missing: refuses if present with different content (the user
    has edited it; we don't silently overwrite). Source is the cloned
    repo's `.claude/hooks/` (NOT package data — it lives at the repo root
    so editing it once updates both the dogfooded behaviour and what the
    installer ships).
    """
    src_hook = _resolve_repo_hook(src_dir)
    dst_hook = workspace / ".claude" / "hooks" / "sandbox-check.sh"
    dst_hook.parent.mkdir(parents=True, exist_ok=True)

    if dst_hook.exists():
        if dst_hook.read_bytes() == src_hook.read_bytes():
            return
        raise SettingsConflictError(
            f"refusing — {dst_hook} exists with different content. Reconcile by "
            f"copying the shipped version from {src_hook} after backing up your edits."
        )
    shutil.copy2(src_hook, dst_hook)
    dst_hook.chmod(0o755)


# --- backwards-compat re-export ----------------------------------------------


# Slice 1 tests imported `merge_user_prompt_submit_hook` from this
# module. Keep the name available so the import surface is stable; the
# implementation now lives in `settings_merger.merge`.
def merge_user_prompt_submit_hook(
    existing: dict,
    our_hook_block: dict,
    settings_path: Path | None = None,  # noqa: ARG001 (kept for sig stability)
) -> dict:
    return settings_merger.merge(existing, our_hook_block)


def _read_data_file(name: str) -> str:
    """Read a bash artifact from src/claude_sandbox/data/ as text."""
    return importlib.resources.files("claude_sandbox.data").joinpath(name).read_text()


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        # Clean up the tmp file if we failed before the rename.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _resolve_repo_hook(src_dir: Path) -> Path:
    """Resolve the shipped sandbox-check.sh under the cloned source tree.

    The hook is NOT package data (it lives at the repo root, outside
    `src/`, so the meta-repo dogfoods the same file the installer ships).
    Falls back to the worktree this module was loaded from — useful for
    tests and local development before the install path is set.
    """
    candidate = src_dir / ".claude" / "hooks" / "sandbox-check.sh"
    if candidate.is_file():
        return candidate

    here = Path(__file__).resolve()
    for parent in here.parents:
        local = parent / ".claude" / "hooks" / "sandbox-check.sh"
        if local.is_file():
            return local
        if (parent / "pyproject.toml").exists():
            break

    raise FileNotFoundError(
        f"could not locate sandbox-check.sh — looked under {src_dir} and the loaded source tree."
    )
