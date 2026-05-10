"""Install orchestrator. Probes -> places container artifacts -> places
workspace artifacts. Idempotent — re-runs after a devcontainer rebuild
re-establish container state without disturbing workspace edits.

Slice 1 inlines two helpers (gitconfig generator, settings merger) that
will graduate to standalone modules in slice 2 (`gitconfig.py`,
`settings_merger.py`). Keeping them inline here keeps the slice 1
diff focused.
"""

from __future__ import annotations

import contextlib
import importlib.resources
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from claude_sandbox import probe

# Container-mode v1 paths. Slice 4 introduces the install_paths
# parameterisation that hosts non-root + host-mode installs.
SHADOW_CLAUDE_PATH = Path("/usr/local/bin/claude")
SHADOW_CLI_PATH = Path("/usr/local/bin/claude-sandbox")
REAL_CLAUDE_PATH = Path("/opt/claude/bin/claude")
GITCONFIG_PATH = Path("/etc/claude-gitconfig")
SRC_DIR_DEFAULT = Path("/opt/claude-sandbox-src")


def install(workspace: Path | None = None) -> None:
    """Run the full install orchestration. Raises on refusal-paths.

    `workspace` defaults to $PWD; tests pass a tmpdir.
    """
    if workspace is None:
        workspace = Path.cwd()
    src_dir = Path(os.environ.get("CLAUDE_SANDBOX_SRC_DIR") or SRC_DIR_DEFAULT)

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


def place_real_claude() -> None:
    """Move ~/.local/bin/claude (Anthropic's installer drop) to /opt/claude/bin/claude.

    The shadow at /usr/local/bin/claude must win on $PATH; moving the
    real binary out of $HOME/.local/bin guarantees that even users with
    that on PATH hit the shadow first.
    """
    REAL_CLAUDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if REAL_CLAUDE_PATH.exists():
        return
    home_local = Path.home() / ".local" / "bin" / "claude"
    if home_local.exists() and not home_local.is_symlink():
        shutil.move(str(home_local), str(REAL_CLAUDE_PATH))
        REAL_CLAUDE_PATH.chmod(0o755)
    elif shutil.which("claude"):
        # Best-effort fallback: copy whatever `claude` resolves to today.
        # Better than refusing — the user can rerun once they've curl-bashed
        # the Anthropic installer.
        resolved = shutil.which("claude")
        if resolved:
            shutil.copy2(resolved, REAL_CLAUDE_PATH)
            REAL_CLAUDE_PATH.chmod(0o755)


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

    Atomic write: tmp + rename. Re-running picks up host gitconfig
    edits since the last invocation.
    """
    user_name = name_override
    user_email = email_override
    if user_name is None:
        user_name = _git_config_get("user.name")
    if user_email is None:
        user_email = _git_config_get("user.email")

    body = (
        "[user]\n"
        f"    name = {user_name}\n"
        f"    email = {user_email}\n"
        '[credential "https://github.com"]\n'
        "    helper = !gh auth git-credential\n"
        "[init]\n"
        "    defaultBranch = main\n"
        "[safe]\n"
        "    directory = *\n"
    )
    GITCONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(GITCONFIG_PATH, body, mode=0o644)


def place_workspace_settings(workspace: Path) -> None:
    """Wire the sandbox-check hook into <workspace>/.claude/settings.json.

    Clean install when missing; one-key surgical merge of
    `hooks.UserPromptSubmit` when present. Refuses on conflict (a
    different hook command pointing at a different script). Never
    touches `permissions`, `env`, or any other key.
    """
    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    hook_block = {
        "hooks": [
            {
                "type": "command",
                "command": ".claude/hooks/sandbox-check.sh",
            }
        ]
    }

    if not settings_path.exists():
        merged = {"hooks": {"UserPromptSubmit": [hook_block]}}
    else:
        existing_text = settings_path.read_text()
        try:
            existing = json.loads(existing_text) if existing_text.strip() else {}
        except json.JSONDecodeError as exc:
            raise SettingsConflictError(
                f"existing {settings_path} is not valid JSON; refusing to overwrite ({exc.msg})."
            ) from exc
        merged = merge_user_prompt_submit_hook(existing, hook_block, settings_path)

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


class SettingsConflictError(RuntimeError):
    """Raised when surgical merge encounters real disagreement."""


def merge_user_prompt_submit_hook(
    existing: dict,
    our_hook_block: dict,
    settings_path: Path | None = None,
) -> dict:
    """Append-with-dedupe our hook block into hooks.UserPromptSubmit.

    Idempotent: re-running yields a byte-identical result. Refuses
    when the existing settings already wires the same hook script with
    a different command (the user has edited the script's invocation —
    don't silently win).
    """
    merged = dict(existing)
    hooks = dict(merged.get("hooks") or {})
    user_prompt_submit = list(hooks.get("UserPromptSubmit") or [])

    our_inner_hooks = our_hook_block.get("hooks", [])
    our_command = next(
        (h.get("command") for h in our_inner_hooks if h.get("type") == "command"),
        None,
    )

    # Search for an existing block that mentions the same hook script
    # (matched by basename of the first whitespace-split token — the
    # user may have absolutised the path or appended args).
    if our_command:
        our_basename = Path(_first_token(our_command)).name
        for block in user_prompt_submit:
            for entry in block.get("hooks", []):
                if entry.get("type") != "command":
                    continue
                cmd = entry.get("command") or ""
                cmd_basename = Path(_first_token(cmd)).name
                if cmd_basename == our_basename:
                    if cmd != our_command:
                        path_hint = f" in {settings_path}" if settings_path else ""
                        raise SettingsConflictError(
                            f"refusing — UserPromptSubmit hook for {our_basename} is "
                            f"already wired with a different command ({cmd}){path_hint}. "
                            f"Reconcile by editing the file directly."
                        )
                    # Same hook already wired — no-op (idempotent).
                    return merged

    user_prompt_submit.append(our_hook_block)
    hooks["UserPromptSubmit"] = user_prompt_submit
    merged["hooks"] = hooks
    return merged


def _first_token(command: str) -> str:
    """Return the first whitespace-delimited token of a command string."""
    return command.split(maxsplit=1)[0] if command.strip() else ""


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


def _git_config_get(key: str) -> str:
    """Return the host's git config value for `key`, empty string on miss."""
    result = subprocess.run(
        ["git", "config", "--get", key],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


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
