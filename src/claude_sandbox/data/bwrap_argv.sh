#!/usr/bin/env bash
# BwrapArgvBuilder: pure function emitting the bwrap argv for the shadow
# `/usr/local/bin/claude`. Pure-by-design so it can be unit-tested in
# isolation — given (workspace, real_claude, "$@") it always emits the
# same argv for the same env.
#
# Strict-under-/root model. The system root is mounted RO, then $HOME
# is wiped via tmpfs, and only the directories Claude legitimately needs
# (.claude, .cache if present) are bound back in. This closes every
# $HOME-based credential tool past, present, and future without
# enumeration.
#
# Inputs (positional):
#   $1 — workspace path to bind RW (typically $PWD at invocation time).
#   $2 — real claude binary path (typically /opt/claude/bin/claude).
#   $@ — remaining args forwarded to claude.
#
# Output: prints the argv on stdout, one element per line. Caller
# composes with mapfile / readarray and execs.

bwrap_argv_build() {
    local workspace="$1"; shift
    local real_claude="$1"; shift

    # v1 is container-only with HOME=/root; the parameterisation is kept
    # so a future host-mode work item can flip it without touching the
    # security-critical body of this function.
    local home="${HOME:-/root}"
    local gitconfig_path="${CLAUDE_SANDBOX_GITCONFIG_PATH:-/etc/claude-gitconfig}"

    local -a argv=(
        bwrap
        --ro-bind / /
        --dev-bind /dev /dev
    )

    # Fresh procfs (--proc /proc) gives the sandbox a per-pid-namespace
    # view, hiding host PIDs. In some nested-container environments
    # (e.g. podman/docker with strict seccomp) mount(MS_PROC) is denied
    # even from inside a fresh user+pid namespace. CLAUDE_SANDBOX_FRESH_PROC=0
    # falls back to a read-only bind of the host /proc — host PIDs become
    # visible (read-only), but the rest of the sandbox still works. The
    # shadow probes at launch and flips this only when forced to.
    if [ "${CLAUDE_SANDBOX_FRESH_PROC:-1}" = "0" ]; then
        argv+=( --ro-bind /proc /proc )
    else
        argv+=( --proc /proc )
    fi

    argv+=(
        --tmpfs /tmp
    )

    # /run/user and /run/secrets are tmpfs-masked ONLY when the host
    # actually has them. In nested containers /run is often read-only
    # and these subdirs may not exist; bwrap would then fail to mkdir
    # the mount point. The mask is a no-op when the source is absent
    # anyway (verify-sandbox checks 13/14 pass trivially against a
    # non-existent path), so we drop the mount entirely in that case.
    if [ -d /run/user ]; then
        argv+=( --tmpfs /run/user )
    fi
    if [ -d /run/secrets ]; then
        argv+=( --tmpfs /run/secrets )
    fi

    argv+=(
        # Strict-under-/root by inversion: wipe $HOME, then bind back
        # only what Claude legitimately needs. Anything we forgot to
        # enumerate stays masked — the whole point of inverting.
        --tmpfs "$home"
    )

    if [ -d "$home/.claude" ]; then
        argv+=( --bind "$home/.claude" "$home/.claude" )
    fi
    if [ -d "$home/.cache" ]; then
        argv+=( --bind "$home/.cache" "$home/.cache" )
    fi

    if [ -n "$workspace" ] && [ -d "$workspace" ]; then
        argv+=( --bind "$workspace" "$workspace" )
    fi

    # Defence-in-depth file masks. Strict-under-/root already hides the
    # dotfiles under $HOME, but masking them with /dev/null is free,
    # explicit, and survives if the strict-root bind ever regresses.
    # /etc/gitconfig is masked unconditionally — it lives outside the
    # inversion. --bind-try keeps the argv valid on hosts where the
    # source path doesn't exist.
    local mask
    for mask in "$home/.gitconfig" /etc/gitconfig "$home/.netrc" \
                "$home/.Xauthority" "$home/.ICEauthority"; do
        argv+=( --bind-try /dev/null "$mask" )
    done

    argv+=(
        --cap-drop ALL
        # --unshare-user-try is required when bwrap runs as root inside
        # a nested container that lacks CAP_SYS_ADMIN — without a user
        # namespace, the kernel refuses to clone the pid/ipc/uts
        # namespaces below. The `-try` variant lets bwrap continue if
        # the kernel forbids userns entirely (the install-time probe
        # would already have refused in that case). When bwrap runs as
        # non-root it implicitly unshares user anyway, so this is a
        # no-op in that path.
        --unshare-user-try
        --unshare-pid
        --unshare-ipc
        --unshare-uts
        --unshare-cgroup-try
        --new-session
        --die-with-parent
    )

    # Scrub the env by default, then re-export only what Claude needs.
    # PATH is set to a known-safe value (NOT the host's), HOME is
    # preserved so tools resolving `~/.claude` find the bound directory,
    # and the gitconfig env-vars are hard-coded so a host-side gitconfig
    # cannot bleed in. DISPLAY is deliberately absent.
    local pass_through_var
    argv+=( --clearenv )
    argv+=( --setenv PATH "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" )
    argv+=( --setenv HOME "$home" )
    # bwrap's default user-namespace remapping maps invoking UID -> 0
    # inside, so id -u inside is always 0. Keeping USER=root consistent
    # with id -u avoids tools that cross-reference the two.
    argv+=( --setenv USER "root" )
    argv+=( --setenv IS_SANDBOX "1" )
    argv+=( --setenv GIT_CONFIG_GLOBAL "$gitconfig_path" )
    argv+=( --setenv GIT_CONFIG_SYSTEM "/dev/null" )
    # TERM / LANG / LC_* are passed through if set on the host so the
    # in-sandbox terminal renders correctly. Anything else stays scrubbed.
    for pass_through_var in TERM LANG LC_ALL LC_CTYPE LC_MESSAGES LC_TIME LC_COLLATE LC_NUMERIC LC_MONETARY; do
        if [ -n "${!pass_through_var:-}" ]; then
            argv+=( --setenv "$pass_through_var" "${!pass_through_var}" )
        fi
    done

    argv+=( -- "$real_claude" "$@" )
    printf '%s\n' "${argv[@]}"
}
