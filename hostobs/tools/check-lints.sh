#!/bin/sh
set -eu

if [ "$#" -gt 1 ]; then
    printf 'usage: check-lints.sh [WORKSPACE]\n' >&2
    exit 2
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
workspace=${1:-"$(dirname -- "$script_dir")"}
exec python3 "$script_dir/check-lints.py" "$workspace"
