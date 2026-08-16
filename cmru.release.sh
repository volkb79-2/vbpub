#!/usr/bin/env bash
# cmru.release.sh — isolated one-shot: snapshot → gate → tag → build → publish
#
# This repo builds & releases its products with **cmru** (Configurable Multi Release
# Utility). This file is the one convenience wrapper → it just runs `cmru release`.
#
#   What/why:  cmru/docs/SPEC.md (start at "S-CLI — CLI at a glance")
#   Config:    cmru.orchestration.toml (root cmru.secret.toml, optional
#              <project>/cmru.secret.toml override, or $GITHUB_PUSH_PAT)
#   All verbs: cmru --help
#   Example:   ./cmru.release.sh            # all changed   /   ./cmru.release.sh --dry-run
#
# The wrapper is the audit-log entry point.  It overwrites cmru.release.log by
# default, while CMRU writes each project step to <project>/logs/cmru/<step>.log.
# Use --log-append to retain the previous run (separated by "---"), and
# --show-run-details when raw build/test output is wanted in this terminal too.
#
# Args pass straight through to cmru.
set -euo pipefail
export PYTHONUNBUFFERED=1

repo_dir="$(dirname "$(readlink -f "$0")")"
cmru_bin="${CMRU_BIN:-cmru}"
if ! command -v "${cmru_bin}" >/dev/null 2>&1; then
    echo "[ERROR] installed cmru command not found: ${cmru_bin}" >&2
    echo "        run cmru/build-initial-standalone.sh, install its wheel, and add it to PATH" >&2
    exit 2
fi

args=("$@")
append_log=false
for arg in "${args[@]}"; do
    if [[ "${arg}" == "--log-append" ]]; then
        append_log=true
    fi
done

log_file="${CMRU_RELEASE_LOG:-$repo_dir/cmru.release.log}"
if ${append_log}; then
    printf '\n---\n' >> "$log_file"
    export CMRU_LOG_APPEND=1
else
    : > "$log_file"
fi
export CMRU_RUN_LOG="$log_file"

# Process substitution preserves live stdout/stderr while teeing concise
# orchestration output. Quiet project detail is appended directly to CMRU_RUN_LOG
# by the runner, so this is a complete debug transcript without console noise.
exec > >(tee -a "$log_file") 2>&1
exec "$cmru_bin" release --config "$repo_dir/cmru.orchestration.toml" "${args[@]}"
