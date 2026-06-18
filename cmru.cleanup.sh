#!/usr/bin/env bash
# cmru.cleanup.sh — prune old releases / GHCR image versions by age
#
# This repo builds & releases its products with **cmru** (Configurable Multi Release
# Utility). This file is a thin, discoverable shim → it just runs `cmru cleanup`.
#
#   What/why:  cmru/docs/SPEC.md (start at "S-CLI — CLI at a glance")
#   Config:    cmru.toml   (token: cmru.secret.toml or $GITHUB_PUSH_PAT)
#   All verbs: ./cmru.py --help
#   Example:   ./cmru.cleanup.sh --remove-assets 30d
#
# Args pass straight through to cmru.
set -euo pipefail
exec "$(dirname "$(readlink -f "$0")")/cmru.py" cleanup "$@"
