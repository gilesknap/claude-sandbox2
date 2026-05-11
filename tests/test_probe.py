"""Mount-scan: given fixture `mount` output, the warning lines emitted
match expectations. Kernel-userns probe is not unit-tested (single
subprocess invocation).
"""

from __future__ import annotations

from claude_sandbox.probe import mount_scan


def test_mount_scan_warns_on_kubeconfig_bind() -> None:
    output = "tmpfs on /kubeconfig type tmpfs (rw,relatime)\n"
    warnings = mount_scan(output)
    assert len(warnings) == 1
    assert "/kubeconfig" in warnings[0]


def test_mount_scan_ignores_standard_mounts() -> None:
    output = "\n".join(
        [
            "proc on /proc type proc (rw,nosuid,nodev,noexec,relatime)",
            "tmpfs on /tmp type tmpfs (rw)",
            "/dev/sda1 on / type ext4 (rw,relatime)",
            "tmpfs on /run/secrets type tmpfs (rw)",
            "/dev/sdb1 on /workspaces type ext4 (rw,relatime)",
            "tmpfs on /var/lib/docker type tmpfs (rw)",
        ]
    )
    assert mount_scan(output) == []


def test_mount_scan_warns_on_token_path() -> None:
    output = "tmpfs on /etc/foo-token type tmpfs (rw)\n"
    warnings = mount_scan(output)
    assert len(warnings) == 1
    assert "/etc/foo-token" in warnings[0]


def test_mount_scan_handles_multiple_warnings_in_order() -> None:
    output = "\n".join(
        [
            "tmpfs on /kubeconfig type tmpfs (rw)",
            "proc on /proc type proc (rw)",
            "tmpfs on /credentials type tmpfs (rw)",
        ]
    )
    warnings = mount_scan(output)
    assert len(warnings) == 2
    assert "/kubeconfig" in warnings[0]
    assert "/credentials" in warnings[1]


def test_mount_scan_returns_empty_on_empty_input() -> None:
    assert mount_scan("") == []
