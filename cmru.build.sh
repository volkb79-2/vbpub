#!/usr/bin/env bash
# cmru.build.sh — build release artifacts only (no upload)
#
# This repo builds & releases its products with **cmru** (Configurable Multi Release
# Utility). This file is a thin, discoverable shim → it just runs `cmru build`.
#
#   What/why:  cmru/docs/SPEC.md (start at "S-CLI — CLI at a glance")
#   Config:    cmru.orchestration.toml
#   All verbs: ./cmru.py --help
#   Example:   ./cmru.build.sh --project cmru
#
# Args pass straight through to cmru.
set -euo pipefail
repo_dir="$(dirname "$(readlink -f "$0")")"
exec "$repo_dir/cmru.py" build --config "$repo_dir/cmru.orchestration.toml" "$@"
