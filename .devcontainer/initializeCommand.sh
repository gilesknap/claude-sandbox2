#!/usr/bin/env bash
# Pre-flight: create host-side dirs for bind mounts before the container
# starts. Docker/podman create missing bind sources as root-owned
# directories, which then fight the user's UID on the host — easier to
# just mkdir as the host user first.
set -euo pipefail

mkdir -p "$HOME/.config/terminal-config/.claude"
