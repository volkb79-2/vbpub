#!/usr/bin/env bash
# cmru.publish.sh — upload built artifacts + .sha256 to the release
#
# This repo builds & releases its products with **cmru** (Configurable Multi Release
# Utility). This file is a thin, discoverable shim → it just runs `cmru publish`.
#
#   What/why:  cmru/docs/SPEC.md (start at "S-CLI — CLI at a glance")
#   Config:    cmru.toml   (token: cmru.secret.toml or $GITHUB_PUSH_PAT)
#   All verbs: ./cmru.py --help
#   Example:   ./cmru.publish.sh --project cmru
#
# Args pass straight through to cmru.
set -euo pipefail
exec "$(dirname "$(readlink -f "$0")")/cmru.py" publish "$@"
