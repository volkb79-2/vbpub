#!/usr/bin/env bash
# cmru.changelog.sh — catalog release history for an immutable tag published before
# CMRU's source-first history default existed.
#
# This discoverable shim runs `cmru changelog`; it writes a marked CHANGES.md entry
# for review and an explicit follow-up commit. It never moves or recreates the tag.
#
#   What/why:  cmru/docs/SPEC.md S-REL.4a
#   Config:    cmru.orchestration.toml
#   All verbs: ./cmru.py --help
#   Example:   ./cmru.changelog.sh --project assay --backfill-tag assay-v0.1.0
#
# Args pass straight through to cmru.
set -euo pipefail
repo_dir="$(dirname "$(readlink -f "$0")")"
exec "$repo_dir/cmru.py" changelog --config "$repo_dir/cmru.orchestration.toml" "$@"
