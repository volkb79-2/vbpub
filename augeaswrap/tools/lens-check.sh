#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
lens_dir="$project_dir/src/augeaswrap/lenses"
shopt -s nullglob
lenses=("$lens_dir"/*.aug)
tests=("$lens_dir"/tests/test_*.aug)

if ((${#lenses[@]} == 0)); then
    if ((${#tests[@]} != 0)); then
        echo "lens-check: test files exist but there are no overlay lenses" >&2
        exit 2
    fi
    exit 0
fi

if ! command -v augparse >/dev/null 2>&1; then
    echo "lens-check: augparse is required when an overlay lens exists (install augeas-tools)" >&2
    exit 2
fi

for lens in "${lenses[@]}"; do
    name="$(basename "$lens")"
    test_file="$lens_dir/tests/test_$name"
    if [[ ! -f "$test_file" ]]; then
        echo "lens-check: missing lens test: $test_file" >&2
        exit 2
    fi
    augparse -I "$lens_dir" "$test_file"
done

for test_file in "${tests[@]}"; do
    name="$(basename "$test_file")"
    lens="$lens_dir/${name#test_}"
    if [[ ! -f "$lens" ]]; then
        echo "lens-check: test has no matching lens: $test_file" >&2
        exit 2
    fi
done
