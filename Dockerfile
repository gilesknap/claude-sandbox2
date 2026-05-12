# Devcontainer base for claude-sandbox (bash-only rewrite).
#
# The DLS ubuntu-devcontainer image already ships the dev-tooling
# baseline (git, curl, ca-certificates, jq, sudo) the bash installer
# needs. Everything else (bubblewrap, just, nodejs, gh) is apt-installed
# by `.devcontainer/claude-sandbox/install.sh` itself, so this
# Dockerfile is intentionally a single FROM.
FROM ghcr.io/diamondlightsource/ubuntu-devcontainer:noble AS developer
