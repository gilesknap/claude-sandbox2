#!/usr/bin/env bash
# Bring the container up to a state where `claude` is the bwrap shadow
# and the workspace venv is ready for dev work.
#
# Order matters:
#   1. Refuse without a git repo — `bash install` clones the source
#      tree and pytest needs git for some checks; failing here gives a
#      clearer error than the cascade below.
#   2. uv sync — produces .venv so `uv run pytest` and IDE integrations
#      work straight away.
#   3. bash install — apt-installs runtime deps, drops the shadow at
#      /usr/local/bin/claude, populates /opt/claude-sandbox-src. The
#      whole point of this devcontainer: without this step, opening
#      the repo gives `claude` from the real binary and the workspace
#      UserPromptSubmit hook fires `BLOCKED: IS_SANDBOX unset` on every
#      prompt. installer.py is idempotent so rebuild is safe.
set -euo pipefail

if [ ! -d .git ]; then
    cat >&2 <<'EOF'

================================================================
ERROR: This directory is not a git repository.

claude-sandbox's `install` script clones the source tree and the
test suite uses git history. Neither works without a git repo.

To fix this, run on the host (outside the devcontainer):

    git init -b main && git add . && git commit -m 'Initial commit'

then rebuild the devcontainer.

================================================================

EOF
    exit 1
fi

uv venv --clear
hash -r
uv sync

bash install
