"""Headless runner for the /verify-sandbox 18-check battery.

The check bodies live in fenced ```bash``` code blocks under
"## Check NN — ..." headings in `.claude/commands/verify-sandbox.md`.
The slash command (consumed by Claude itself) and this CLI runner share
the same source — if a check changes there, both pick it up on the next
run.

Exit code: non-zero on any FAIL, 0 on all-pass. CI relies on this for
its assertion.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# The names map to the spec's "## Check NN — <name>" headings. Hard-
# coded here so the runner's output looks identical to what a Claude-
# driven /verify-sandbox emits, even though we don't parse the heading
# text out of the markdown. The order is the spec order.
CHECK_NAMES: list[str] = [
    "IS_SANDBOX sentinel set",
    "bwrap is PID-1 ancestor",
    "strict-under-/root: only .claude (+.cache) under $HOME",
    "env scrub: GH_TOKEN empty",
    "env scrub: DISPLAY empty",
    "cap_drop ALL: CapEff=0000000000000000",
    "--unshare-pid: host PID 1 (systemd/init) not visible",
    "--unshare-ipc: ipcns symlink present",
    "--unshare-uts: utsns symlink present",
    "--share-net: outbound TCP to api.anthropic.com:443 OK",
    "--new-session: no controlling tty (TIOCSTI blocked)",
    "/tmp tmpfs: no vscode-ipc-*.sock visible",
    "/run/user empty",
    "/run/secrets empty (Docker/Compose secrets masked)",
    "file mask: $HOME/.gitconfig is empty",
    "file mask: $HOME/.netrc is empty",
    "file mask: $HOME/.Xauthority is empty",
    "curated gitconfig: GIT_CONFIG_GLOBAL set, user.email present",
]

TOTAL_CHECKS = len(CHECK_NAMES)


def extract_check(spec: str, n: int) -> str:
    """Return the body of the FIRST ```bash``` fenced block under
    "## Check NN — " for the given check number.

    Pure function — same input always produces the same body. Slice
    1's only Python-side test target.
    """
    nn = f"{n:02d}"
    # State machine: walk the spec line by line, enter `in_check` at the
    # heading, then capture lines between the first ```bash fence and
    # its closing ``` fence.
    in_check = False
    in_block = False
    captured: list[str] = []
    heading_re = re.compile(rf"^## Check {nn} — ")
    bash_open_re = re.compile(r"^```bash\s*$")
    fence_close_re = re.compile(r"^```\s*$")
    next_heading_re = re.compile(r"^## ")

    for line in spec.splitlines():
        if not in_check:
            if heading_re.match(line):
                in_check = True
            continue
        if in_block:
            if fence_close_re.match(line):
                break
            captured.append(line)
            continue
        if bash_open_re.match(line):
            in_block = True
            continue
        # Defensive: a sibling heading before the bash fence means the
        # spec is malformed for this check.
        if next_heading_re.match(line):
            break

    return "\n".join(captured)


def find_spec(workspace: Path | None = None) -> Path | None:
    """Locate the verify-sandbox markdown.

    The workspace copy wins (it's what the slash command sees) so the
    CLI runner stays in sync with the user's edits. Falls back to the
    cloned source tree.
    """
    candidates: list[Path] = []
    env_override = os.environ.get("CLAUDE_SANDBOX_VERIFY_SPEC")
    if env_override:
        candidates.append(Path(env_override))
    if workspace is not None:
        candidates.append(workspace / ".claude" / "commands" / "verify-sandbox.md")
    candidates.append(Path.cwd() / ".claude" / "commands" / "verify-sandbox.md")
    candidates.append(Path("/opt/claude-sandbox-src/.claude/commands/verify-sandbox.md"))
    # Source-tree fallback: walk up from this file to find the repo root.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / ".claude" / "commands" / "verify-sandbox.md")
        if (parent / "pyproject.toml").exists():
            break

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def run(workspace: Path | None = None) -> int:
    """Run all 18 checks, print the spec's output format, return exit code."""
    spec_path = find_spec(workspace)
    if spec_path is None:
        print(
            "verify-sandbox: spec markdown not found "
            "(tried CLAUDE_SANDBOX_VERIFY_SPEC, $PWD/.claude, /opt/claude-sandbox-src/...)",
            flush=True,
        )
        return 2

    spec = spec_path.read_text()
    print(f"/verify-sandbox: {TOTAL_CHECKS} checks")
    fail = 0
    pass_ = 0
    for n in range(1, TOTAL_CHECKS + 1):
        body = extract_check(spec, n)
        nn = f"{n:02d}"
        name = CHECK_NAMES[n - 1]
        if not body:
            print(f"  [FAIL] {nn} {name} — check body could not be extracted from spec")
            fail += 1
            continue
        # Run with stdin closed so the --new-session check (no
        # controlling tty) behaves the same as the in-sandbox case.
        result = subprocess.run(
            ["bash", "-c", body],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            print(f"  [PASS] {nn} {name}")
            pass_ += 1
        else:
            print(f"  [FAIL] {nn} {name}")
            fail += 1

    print(f"  Summary: {pass_} PASS / {fail} FAIL")
    if fail == 0:
        print("RESULT: SANDBOX OK")
        return 0
    print("RESULT: SANDBOX LEAKING — open an issue against gilesknap/claude-sandbox")
    return 1
