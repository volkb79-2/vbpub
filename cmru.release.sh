#!/usr/bin/env bash
# cmru.release.sh — isolated one-shot: snapshot → gate → tag → build → publish
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

repo_dir="$(dirname "$(readlink -f "$0")")"
# cmru itself is stdlib-only; the wheel-build step's toolchain runs in a dedicated
# container (see wheel-builder/Dockerfile, cmru.toml's [env] CMRU_WHEEL_BUILDER_IMAGE)
# rather than needing anything pre-installed in the host interpreter.
python_bin="${CMRU_PYTHON:-python3}"

# Releases always start fresh (S-CLI.1/S-CLI.5): default to sweeping any previous
# failed attempt(s) in scope before this one begins, unless the caller already
# asked to --resume one explicitly or passed their own --abandon.
args=("$@")
has_resume_or_abandon=false
for arg in "${args[@]}"; do
    case "${arg}" in
        --resume|--resume=*|--abandon|--abandon=*)
            has_resume_or_abandon=true
            break
            ;;
    esac
done
if ! ${has_resume_or_abandon}; then
    args=(--abandon all-previous "${args[@]}")
fi

exec "$python_bin" -u "$repo_dir/cmru.py" release "${args[@]}"
