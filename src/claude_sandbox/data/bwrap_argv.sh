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
        # Fresh /dev (not --dev-bind) hides the host's /dev/pts so an
        # in-sandbox ioctl(TIOCSTI) can only inject into the script(1)-
        # allocated pty the shadow wraps us in — bytes land in *that*
        # pty's input queue, which script's parent reads and writes as
        # output to the host terminal (displayed, not enqueued). The
        # host shell's input buffer is unreachable. This replaces the
        # --new-session TIOCSTI defence below; see the resize-fix issue
        # in README-CLAUDE.md.
        --dev /dev
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

    # `$home/.claude` may be a real directory or a symlink to
    # `/user-terminal-config/.claude` (the shared cross-container tree
    # bound in by devcontainer.json; the installer sets up that symlink
    # when the mount is present). Both forms work: `-d` follows the
    # symlink and `--bind` resolves the source on the host fs, so the
    # symlink target ends up bound writably at $home/.claude inside
    # the sandbox. The symlink itself is shadowed by the tmpfs above
    # — only the resolved content is visible to Claude.
    if [ -d "$home/.claude" ]; then
        argv+=( --bind "$home/.claude" "$home/.claude" )
    fi
    # Claude Code stores account state (OAuth token, recent-projects
    # list, settings) in ~/.claude.json — a top-level *file*, not under
    # the ~/.claude/ directory. Without this bind the file lives in the
    # in-sandbox tmpfs, so every fresh `claude` launch starts unauth'd.
    # Shadow pre-creates the file before launch so a first-time login
    # has somewhere to write.
    if [ -f "$home/.claude.json" ]; then
        argv+=( --bind "$home/.claude.json" "$home/.claude.json" )
    fi
    if [ -d "$home/.cache" ]; then
        argv+=( --bind "$home/.cache" "$home/.cache" )
    fi

    # Selective credential exposure: gh (~/.config/gh) and glab
    # (~/.config/glab-cli) are the only host credential paths the
    # sandbox trusts. Everything else under $HOME — SSH keys, VS Code
    # cred helpers, cloud SDK caches, etc. — stays masked by the
    # strict-under-/root inversion. Keep this list narrow: every entry
    # is a weakening of the inversion. bwrap auto-creates $home/.config
    # as an empty tmpfs intermediate, so the sibling subdirs are not
    # incidentally exposed.
    local cred_subdir
    for cred_subdir in .config/gh .config/glab-cli; do
        if [ -d "$home/$cred_subdir" ]; then
            argv+=( --bind "$home/$cred_subdir" "$home/$cred_subdir" )
        fi
    done

    # Selective tooling exposure: uv-managed Python interpreters live
    # at ~/.local/share/uv/python/... and the project's .venv/bin/python
    # is a symlink into that tree. Without these binds the symlink
    # target is in the strict-under-/root tmpfs and resolves to nothing
    # — `python`, `uv run`, and any `.venv/bin/*` invocation fails. The
    # `uv` binary itself lives at ~/.local/bin/uv (and uvx if installed
    # via the astral installer), so we bind those files individually
    # rather than the whole ~/.local/bin (which Claude Code also
    # writes into via tmpfs at runtime and we don't want those writes
    # to leak back onto the host). $HOME/.local/bin is appended to
    # PATH below so `uv` resolves without a full path. Same trust
    # footprint as gh/glab: this is a tool cache, not credential
    # storage — but it does mean a malicious uv-installed binary on
    # the host could be invoked from inside the sandbox.
    if [ -d "$home/.local/share/uv" ]; then
        argv+=( --bind "$home/.local/share/uv" "$home/.local/share/uv" )
    fi
    local uv_bin
    for uv_bin in uv uvx; do
        if [ -f "$home/.local/bin/$uv_bin" ]; then
            argv+=( --bind "$home/.local/bin/$uv_bin" "$home/.local/bin/$uv_bin" )
        fi
    done

    # Claude Code's self-check reads `installMethod` from ~/.claude/
    # config and, when it's `native` (Anthropic's installer's default),
    # expects to find the binary at `~/.local/bin/claude`. Without this
    # bind that path is on the strict-under-/root tmpfs and the warning
    # "claude command not found at /root/.local/bin/claude" fires on
    # every launch. Bind the same real binary the shadow exec's
    # ($real_claude = <src_dir>/.runtime/claude) so the self-check sees
    # exactly what's running. --bind-try is harmless if $real_claude is
    # absent (the shadow already refuses to launch in that case).
    if [ -f "$real_claude" ]; then
        argv+=( --bind-try "$real_claude" "$home/.local/bin/claude" )
    fi

    if [ -n "$workspace" ] && [ -d "$workspace" ]; then
        argv+=( --bind "$workspace" "$workspace" )
    fi

    # Defence-in-depth file masks. Strict-under-/root already hides the
    # dotfiles under $HOME, but masking them with /dev/null is free,
    # explicit, and survives if the strict-root bind ever regresses.
    # /etc/shadow, /etc/gshadow, and /etc/sudoers live outside the
    # inversion and are masked when present. /etc/shadow leaks the
    # host user list (and password hashes on hosts where users have
    # passwords); /etc/sudoers leaks the sudo policy. Both are
    # information-disclosure rather than credential exfil under
    # cap-drop ALL + NO_NEW_PRIVS, but masking is free.
    #
    # Gitconfigs are deliberately NOT masked. The host's
    # /root/.gitconfig is already invisible via strict-under-/root,
    # and host /etc/gitconfig is neutralised by the env redirect that
    # follows (GIT_CONFIG_GLOBAL=/etc/claude-gitconfig,
    # GIT_CONFIG_SYSTEM=/dev/null). The bind-mask we previously layered
    # on top broke tools like pre-commit that scrub GIT_* env before
    # spawning a child `git init` (pre_commit/git.py::no_git_env): with
    # the env redirect gone, the child git fell back to reading the
    # masked /etc/gitconfig and on EL9 + SELinux that returned EACCES
    # instead of empty content, aborting every hook.
    #
    # $HOME masks always emit: $home is on tmpfs so bwrap can create
    # the destination mount point, and --bind-try short-circuits when
    # the source is absent.
    #
    # /etc masks are gated on the invoking user being able to *read*
    # the host file:
    #   - if the user can't read it (e.g. /etc/shadow mode 0000, or
    #     /etc/sudoers mode 0440 for a non-root invoker), there is no
    #     leak to mask — Claude inside the sandbox runs as the same
    #     real UID under the user namespace and cannot read it either.
    #   - bwrap setting up a bind on /etc/<file> under a --ro-bind / /
    #     fails on these hosts (the destination resolution in the new
    #     namespace dies with EROFS even though the host file exists),
    #     so emitting the mask unconditionally aborts every launch.
    local mask
    for mask in "$home/.netrc" "$home/.Xauthority" "$home/.ICEauthority"; do
        argv+=( --bind-try /dev/null "$mask" )
    done
    for mask in /etc/shadow /etc/gshadow /etc/sudoers; do
        if [ -r "$mask" ]; then
            argv+=( --bind /dev/null "$mask" )
        fi
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
        # --new-session was dropped intentionally: setsid() detached
        # the sandbox from its controlling terminal, which killed
        # SIGWINCH delivery (resize stops propagating) and broke job
        # control. The TIOCSTI defence it provided is now delivered
        # by the shadow wrapping bwrap in script(1) plus --dev /dev
        # above (fresh devpts, host /dev/pts not visible). See the
        # resize-fix issue in README-CLAUDE.md.
        --die-with-parent
    )

    # Scrub the env by default, then re-export only what Claude needs.
    # PATH is set to a known-safe value (NOT the host's), HOME is
    # preserved so tools resolving `~/.claude` find the bound directory,
    # and the gitconfig env-vars are hard-coded so a host-side gitconfig
    # cannot bleed in. DISPLAY is deliberately absent.
    local pass_through_var
    argv+=( --clearenv )
    # $HOME/.local/bin is appended (not prepended) so system tools
    # take precedence in PATH resolution — a malicious binary written
    # to ~/.local/bin/<sysname> can't hijack standard commands. Only
    # `uv` / `uvx` (bound from host, see above) and Claude Code's
    # tmpfs-written helpers actually live there.
    argv+=( --setenv PATH "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$home/.local/bin" )
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
