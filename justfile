# claude-sandbox recipes. Run `just <recipe>` from the repo root.

# Run the bash test suite.
test:
    bash tests/bwrap_argv.sh
    CLAUDE_SANDBOX_SMOKE=1 bash tests/smoke.sh

# Pull the latest tip and re-run the installer.
upgrade:
    git pull --ff-only
    sudo bash install

# Authenticate gh CLI with a GitHub PAT (token not stored in shell history).
gh-auth:
    #!/usr/bin/env bash
    cat <<'EOF'
    Create or renew a fine-grained PAT at:
      https://github.com/settings/personal-access-tokens

    Recommended settings for a sandboxed Claude Code:
      - Resource owner: your user (or org that owns this repo)
      - Repository access: Only select repositories -> just this repo
      - Expiration: short (e.g. 30 days) so a leaked token expires quickly
      - Repository permissions (Read and Write):
          Contents, Issues, Pull requests
        (Metadata: Read-only is added automatically)
      - Leave everything else unset / no access

    EOF
    read -sp "GitHub PAT: " t && echo
    echo "$t" | gh auth login --with-token
    unset t
    gh auth setup-git
    gh auth status

# Authenticate glab CLI with a GitLab PAT (token not stored in shell history).
# --git-protocol https prevents glab's SSH insteadOf rewrite.
glab-auth hostname="gitlab.com":
    #!/usr/bin/env bash
    cat <<'EOF'
    Create or renew a fine-grained PAT at:
      https://gitlab.com/-/user_settings/personal_access_tokens
      (or your organisation's GitLab instance equivalent)

    Recommended scopes for a sandboxed Claude Code:
      - api, read_repository, write_repository
      - Short expiration so a leaked token expires quickly

    EOF
    read -sp "GitLab PAT for {{ hostname }}: " t && echo
    echo "$t" | glab auth login --stdin --hostname {{ hostname }} --git-protocol https
    unset t
    glab auth status

# Reminder for the canonical live security battery.
verify:
    @echo "Run /verify-sandbox inside a Claude session to run the live 16-check battery + 10 breakout probes."
