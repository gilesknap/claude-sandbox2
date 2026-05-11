# Devcontainer base for claude-sandbox.
#
# The DLS ubuntu-devcontainer image already includes the dev-tooling
# baseline (uv, git, curl, ca-certs, jq, sudo, build essentials) that
# the `install` script and pytest suite need. Everything else the
# project requires at runtime — bubblewrap, nodejs, gh — is apt-installed
# by `install` itself, so this Dockerfile is intentionally a single FROM.
# Future apt deps that aren't already in the base or installable by
# `install` should land here.
FROM ghcr.io/diamondlightsource/ubuntu-devcontainer:noble AS developer
