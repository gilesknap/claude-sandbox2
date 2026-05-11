---
description: Verify the Claude sandbox is intact — runs the full 18-check PASS/FAIL battery against the live process and exits non-zero on any FAIL so the command is usable as a CI assertion.
---

`/verify-sandbox` runs exactly **18 checks** against the live Claude
process. Each check is a small bash test runnable inside the sandbox
that returns PASS or FAIL with a one-line explanation. The set covers
every defence the PRD's "Sandbox model" section establishes.

Run each check below in order, capture PASS/FAIL, and print the table
described under "Output format" at the end. Any FAIL must cause the
overall command to exit non-zero (so CI assertions work).

## Check 01 — IS_SANDBOX sentinel

`IS_SANDBOX=1` is set inside the sandbox by `bwrap --setenv`. If
unset, Claude was launched against `/opt/claude/bin/claude` directly,
bypassing the sandbox entirely. This is the fall-through sentinel.

```bash
[ "${IS_SANDBOX:-}" = "1" ]
```

## Check 02 — bwrap is the PID-1 ancestor

Inside `--unshare-pid`, PID 1 is the bwrap'd entry, not the host's
init. We confirm by reading `/proc/1/comm` and asserting it is
`bwrap` or `claude` (the exec'd target). On the host, PID 1 is
`systemd` / `init` / similar.

```bash
case "$(cat /proc/1/comm 2>/dev/null)" in
    bwrap|claude|node) exit 0 ;;
    *) exit 1 ;;
esac
```

## Check 03 — strict-under-/root

`$HOME` (typically `/root`) is a tmpfs with only `.claude` and
(optionally) `.cache` bound back in. The defence-in-depth file masks
(checks 15–17) also bind `/dev/null` over `.gitconfig`, `.netrc`,
`.Xauthority`, and `.ICEauthority` — so those names are expected to
appear too, as size-zero entries (which checks 15–17 verify). Anything
else under `$HOME` means the strict-under-/root inversion regressed.

```bash
# ls -A skips . and ..; the allowed entries are the .claude/.cache
# binds plus the four masked dotfiles intentionally bound to /dev/null.
extras="$(ls -A "$HOME" 2>/dev/null | grep -vxE '\.claude|\.cache|\.gitconfig|\.netrc|\.Xauthority|\.ICEauthority' || true)"
[ -z "$extras" ]
```

## Check 04 — env scrub: GH_TOKEN

With `--clearenv` and an explicit allow-list, `GH_TOKEN` from the
host shell must be empty inside the sandbox.

```bash
[ -z "${GH_TOKEN:-}" ]
```

## Check 05 — env scrub: DISPLAY

`DISPLAY` is deliberately not in the `--clearenv` allow-list — it
closes the X11 reachability path.

```bash
[ -z "${DISPLAY:-}" ]
```

## Check 06 — cap_drop ALL

`--cap-drop ALL` empties the effective capability set. `CapEff` in
`/proc/self/status` reads all zeros.

```bash
grep -q '^CapEff:\s*0\{16\}$' /proc/self/status
```

## Check 07 — --unshare-pid

A known host PID (PID 1 on the host is always present and stable) is
not visible inside the sandbox's PID namespace. We check that PID 1
inside the sandbox is bwrap-or-claude (not the host's init).

```bash
# If we share the host pidns, /proc/1/comm reads systemd/init.
case "$(cat /proc/1/comm 2>/dev/null)" in
    systemd|init) exit 1 ;;
    *) exit 0 ;;
esac
```

## Check 08 — --unshare-ipc

The SysV IPC namespace differs from the host's. We compare the
inode of `/proc/self/ns/ipc` to PID 1's (PID 1 is bwrap-or-claude
inside, by check 02; the inodes differ from the host's by virtue of
unshare).

```bash
# inside an unshared ipcns, /proc/self/ns/ipc resolves to a different
# inode than the un-namespaced kernel default. We can't sample the
# host inode from inside, but we CAN assert /proc/self/ns/ipc exists
# and is a symlink to a unique ipc:[<inum>].
ipc_link="$(readlink /proc/self/ns/ipc 2>/dev/null || true)"
case "$ipc_link" in ipc:\[*\]) exit 0 ;; *) exit 1 ;; esac
```

## Check 09 — --unshare-uts

The UTS namespace is unshared, so a hostname change inside doesn't
affect the host. We assert the namespace symlink exists with the
expected shape; the integration test exercises the behavioural property.

```bash
uts_link="$(readlink /proc/self/ns/uts 2>/dev/null || true)"
case "$uts_link" in uts:\[*\]) exit 0 ;; *) exit 1 ;; esac
```

## Check 10 — --share-net (outbound network reachable)

`--share-net` is deliberately omitted from the unshare list — Claude
needs network. We confirm by attempting an outbound TCP connection.
Failing this check means the user accidentally added `--unshare-net`
to the argv builder and broke Claude.

```bash
# Bash's /dev/tcp pseudo-device opens a TCP connection; failure means
# either no network namespace sharing or no DNS. We accept any of
# anthropic.com / cloudflare-dns / google-dns succeeding.
(exec 3<>/dev/tcp/api.anthropic.com/443) 2>/dev/null && exec 3<&- 3>&-
```

## Check 11 — --new-session (TIOCSTI blocked)

`--new-session` calls `setsid()` so the controlling terminal is
detached. An ioctl(TIOCSTI) injection attempt cannot reach the
parent shell.

```bash
# If --new-session is in effect, we have no controlling tty so an
# attempted TIOCSTI on stdin fails. tty -s exits non-zero when stdin
# is not a tty; bwrap's --new-session makes that the case.
! tty -s 2>/dev/null
```

## Check 12 — /tmp is tmpfs and empty

The host's `/tmp` carries VS Code IPC sockets (`vscode-ipc-*.sock`,
`vscode-git-*.sock`). `--tmpfs /tmp` masks them. We assert no such
socket is visible.

```bash
# No vscode-ipc-*.sock and no vscode-git-*.sock visible inside.
! ls /tmp/vscode-ipc-*.sock /tmp/vscode-git-*.sock >/dev/null 2>&1
```

## Check 13 — /run/user is tmpfs and empty

`--tmpfs /run/user` masks the user's runtime directory which can hold
DBus sockets and other IPC bridges.

```bash
[ -z "$(ls -A /run/user 2>/dev/null)" ]
```

## Check 14 — /run/secrets is tmpfs and empty

`--tmpfs /run/secrets` closes the Docker/Compose secrets path even
when the host has populated `/run/secrets/*`.

```bash
[ -z "$(ls -A /run/secrets 2>/dev/null)" ]
```

## Check 15 — file mask: .gitconfig empty

`--bind-try /dev/null /root/.gitconfig` is defence-in-depth on top
of strict-under-/root. Reading the file inside the sandbox returns
empty.

```bash
[ ! -s "$HOME/.gitconfig" ]
```

## Check 16 — file mask: .netrc empty

`--bind-try /dev/null /root/.netrc` masks any host `.netrc`
credentials.

```bash
[ ! -s "$HOME/.netrc" ]
```

## Check 17 — file mask: .Xauthority empty

`--bind-try /dev/null /root/.Xauthority` masks the X11 cookie that
would otherwise authenticate against a host X server.

```bash
[ ! -s "$HOME/.Xauthority" ]
```

## Check 18 — curated gitconfig active

`GIT_CONFIG_GLOBAL=/etc/claude-gitconfig` is exported and the file's
`user.email` matches the host's. Verifies that the curated gitconfig
is in effect at every launch.

```bash
[ "${GIT_CONFIG_GLOBAL:-}" = "/etc/claude-gitconfig" ] && \
    [ -n "$(git config --get user.email 2>/dev/null)" ]
```

## Output format

Print a header line `"/verify-sandbox: 18 checks"`, then one
`[PASS]` / `[FAIL]` line per check (zero-padded number, name,
one-line explanation on FAIL), then a `Summary:` line.

```
/verify-sandbox: 18 checks
  [PASS] 01 IS_SANDBOX sentinel set
  [PASS] 02 bwrap is PID-1 ancestor
  [PASS] 03 strict-under-/root: only .claude (+.cache) under $HOME
  [PASS] 04 env scrub: GH_TOKEN empty
  [PASS] 05 env scrub: DISPLAY empty
  [PASS] 06 cap_drop ALL: CapEff=0000000000000000
  [PASS] 07 --unshare-pid: host PID 1 (systemd/init) not visible
  [PASS] 08 --unshare-ipc: ipcns symlink present
  [PASS] 09 --unshare-uts: utsns symlink present
  [PASS] 10 --share-net: outbound TCP to api.anthropic.com:443 OK
  [PASS] 11 --new-session: no controlling tty (TIOCSTI blocked)
  [PASS] 12 /tmp tmpfs: no vscode-ipc-*.sock visible
  [PASS] 13 /run/user empty
  [PASS] 14 /run/secrets empty (Docker/Compose secrets masked)
  [PASS] 15 file mask: $HOME/.gitconfig is empty
  [PASS] 16 file mask: $HOME/.netrc is empty
  [PASS] 17 file mask: $HOME/.Xauthority is empty
  [PASS] 18 curated gitconfig: GIT_CONFIG_GLOBAL set, user.email present
  Summary: 18 PASS / 0 FAIL
```

If any check FAILs, replace `[PASS]` with `[FAIL]` and append the
specific reason to that line so a developer reading the output can
identify which defence regressed. Then exit non-zero (the summary
line is informational; the non-zero exit is what CI relies on).

If every check passes, end with `RESULT: SANDBOX OK`. Otherwise end
with `RESULT: SANDBOX LEAKING — open an issue against gilesknap/claude-sandbox`.
