"""Environment introspection. Pure-read; refuses with named, actionable
diagnostics on failure.

The probe runs at install time AND at every shadow-claude launch
(defence against runtime drift). Refusal paths raise typed exceptions
the CLI converts to non-zero exit + stderr; mount-scan only warns.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


class UnsupportedHostError(RuntimeError):
    """Raised when the host distro / runtime is outside the v1 envelope."""


class UserNamespacesBlockedError(RuntimeError):
    """Raised when unprivileged user namespaces are unavailable."""


def apt_or_refuse() -> None:
    """v1 only supports Debian/Ubuntu — refuse on any other distro."""
    if shutil.which("apt-get") is None:
        raise UnsupportedHostError(
            "claude-sandbox: refusing — claude-sandbox v1 supports Debian/Ubuntu only "
            "(no apt-get on PATH). File an issue at gilesknap/claude-sandbox2 to request "
            "your distro."
        )


def kernel_userns_or_refuse() -> None:
    """Confirm unprivileged user-namespace creation works.

    Runs before any state mutation, using only tools present on every
    Debian/Ubuntu install (`unshare` from util-linux). The wrap-time
    bwrap probe is a separate function (`bwrap_or_refuse`) because
    bwrap is an installed dependency that does not exist on the first
    run of `install`.
    """
    if shutil.which("unshare") is None:
        raise UnsupportedHostError(
            "claude-sandbox: refusing — `unshare` (util-linux) is not on PATH; "
            "cannot probe user namespaces."
        )
    result = subprocess.run(
        ["unshare", "--user", "--pid", "--fork", "--map-root-user", "true"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise UserNamespacesBlockedError(_diagnose_userns())


def bwrap_or_refuse() -> None:
    """Confirm the installed bwrap can establish the namespaces we need.

    Runs after the package installer has put bubblewrap on the host.
    Establishes that the binary is not just present but actually
    functional on this kernel + AppArmor combination.
    """
    if shutil.which("bwrap") is None:
        raise UnsupportedHostError(
            "claude-sandbox: refusing — bwrap not on PATH after install. "
            "Sandbox would not work on this host."
        )
    result = subprocess.run(
        ["bwrap", "--unshare-user", "--unshare-pid", "--tmpfs", "/tmp", "--", "true"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise UserNamespacesBlockedError(
            _diagnose_userns() + "\nclaude-sandbox: refusing — bwrap probe failed after install "
            "(see causes above). Sandbox would not work on this host."
        )


def _diagnose_userns() -> str:
    """Return the most likely cause + fix for a userns/bwrap failure.

    Shared by install-time and runtime refusal paths so both surface the
    same actionable diagnostic.
    """
    sysctl_path = Path("/proc/sys/kernel/unprivileged_userns_clone")
    max_path = Path("/proc/sys/user/max_user_namespaces")
    apparmor_path = Path("/sys/module/apparmor/parameters/enabled")

    if sysctl_path.is_file():
        try:
            if sysctl_path.read_text().strip() == "0":
                return (
                    "claude-sandbox: refusing — kernel.unprivileged_userns_clone=0 "
                    "disables rootless user namespaces. Fix: set sysctl "
                    "kernel.unprivileged_userns_clone=1 on the host."
                )
        except OSError:
            pass
    if max_path.is_file():
        try:
            if max_path.read_text().strip() == "0":
                return (
                    "claude-sandbox: refusing — /proc/sys/user/max_user_namespaces is 0. "
                    "Fix: set sysctl user.max_user_namespaces to a positive value "
                    "(e.g. 15000) on the host."
                )
        except OSError:
            pass
    if apparmor_path.is_file():
        try:
            if apparmor_path.read_text().strip() == "Y":
                return (
                    "claude-sandbox: refusing — AppArmor likely blocks unprivileged "
                    "user namespaces (typical of rootful Docker with the default "
                    "profile). Fix: switch to rootless podman, or run with "
                    "--security-opt apparmor=unconfined, or load a profile that "
                    "allows userns_create."
                )
        except OSError:
            pass

    return (
        "claude-sandbox: refusing — kernel userns/PID probe failed. Check: rootful "
        "Docker default AppArmor profile, kernel.unprivileged_userns_clone=0, "
        "/proc/sys/user/max_user_namespaces=0, or seccomp policy. v1 targets rootless "
        "podman + Debian/Ubuntu."
    )


# Standard kernel / devcontainer mount points we never want to warn
# about. Prefix entries match `/proc`, `/proc/foo`, etc; exact entries
# match the literal target only.
_STANDARD_MOUNT_PREFIXES = (
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/tmp",
    "/workspaces",
    "/opt",
    "/root",
    "/home",
    "/usr",
    "/var",
    "/boot",
    "/mnt",
    "/media",
)
_STANDARD_MOUNT_EXACT = ("/", "/etc/resolv.conf", "/etc/hostname", "/etc/hosts")
_CRED_REGEX = re.compile(
    r"(?:^/(?:kubeconfig|secrets|credentials)$)|(?:cred|secret|token|key)",
    re.IGNORECASE,
)
# Parses a `mount` line like "src on /target type ext4 (opts)".
_MOUNT_LINE_REGEX = re.compile(r"^.* on (?P<target>\S+) type \S+ ")


def mount_scan(mount_output: str | None = None) -> list[str]:
    """Warn about non-standard host bind-mounts that look like credentials.

    Returns one warning string per suspect mount target — the caller
    prints them to stderr. Never refuses; the user adds masks via a
    follow-up workflow.

    Pass `mount_output` for testability; default reads from `mount`.
    """
    if mount_output is None:
        try:
            result = subprocess.run(
                ["mount"],
                check=False,
                capture_output=True,
                text=True,
            )
            mount_output = result.stdout
        except FileNotFoundError:
            return []

    warnings: list[str] = []
    for line in mount_output.splitlines():
        m = _MOUNT_LINE_REGEX.match(line)
        if not m:
            continue
        target = m.group("target")
        if _is_standard_mount(target):
            continue
        if _CRED_REGEX.search(target):
            warnings.append(
                f"claude-sandbox: warning — host bind-mount at {target} looks like a "
                f"credential path. v1 ships no extra-mask override; the strict-under-/root "
                f"inversion already masks anything under $HOME, so the warning is "
                f"informational unless the path is outside $HOME."
            )
    return warnings


def _is_standard_mount(target: str) -> bool:
    if target in _STANDARD_MOUNT_EXACT:
        return True
    return any(
        target == prefix or target.startswith(prefix + "/") for prefix in _STANDARD_MOUNT_PREFIXES
    )
