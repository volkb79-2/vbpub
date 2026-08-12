#!/usr/bin/env bash
# cmru.cleanup.sh — prune old releases / GHCR image versions by age
#
# This repo builds & releases its products with **cmru** (Configurable Multi Release
# Utility). This file is a thin, discoverable shim → it just runs `cmru cleanup`.
#
#   What/why:  cmru/docs/SPEC.md (start at "S-CLI — CLI at a glance")
#   Config:    cmru.orchestration.toml
#   All verbs: ./cmru.py --help
#   Example:   ./cmru.cleanup.sh --remove-assets 30d
#
# Args pass straight through to cmru.
set -euo pipefail
repo_dir="$(dirname "$(readlink -f "$0")")"
exec "$repo_dir/cmru.py" cleanup --config "$repo_dir/cmru.orchestration.toml" "$@"
