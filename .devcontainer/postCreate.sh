#!/usr/bin/env bash
# postCreate: run the bash installer. Idempotent so devcontainer
# rebuilds re-establish the shadow without re-downloading Claude.
set -euo pipefail

bash install
