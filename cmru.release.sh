#!/usr/bin/env bash
# cmru.release.sh — the one-shot: detect changed → tag → push → build → publish
#
# This repo builds & releases its products with **cmru** (Configurable Multi Release
# Utility). This file is a thin, discoverable shim → it just runs `cmru release`.
#
#   What/why:  cmru/docs/SPEC.md (start at "S-CLI — CLI at a glance")
#   Config:    cmru.toml   (token: cmru.secret.toml or $GITHUB_PUSH_PAT)
#   All verbs: ./cmru.py --help
#   Example:   ./cmru.release.sh            # all changed   /   ./cmru.release.sh --dry-run
#
# Args pass straight through to cmru.
set -euo pipefail
export PYTHONUNBUFFERED=1
exec "$(dirname "$(readlink -f "$0")")/cmru.py" release "$@"
