#!/usr/bin/env bash
# Prove that the endpoint suite rejects a disabled reserved-key guard.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(cd "$project_dir/.." && pwd)"
python_bin="${AUGEASWRAP_PYTHON:-python3}"
cli_extended_src="$repo_dir/libraries/cli-extended/src"
if ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "canary: no usable Python interpreter at $python_bin" >&2
    exit 2
fi

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
cp -a "$project_dir/src" "$project_dir/tests" "$project_dir/docs" \
    "$project_dir/pyproject.toml" "$work_dir/"

if ! "$python_bin" - "$work_dir/src/augeaswrap/endpoints.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
before = "        if _is_reserved(key):"
after = "        if False:"
if before not in text:
    raise SystemExit("canary target moved; refusing to claim it was tested")
path.write_text(text.replace(before, after, 1), encoding="utf-8")
PY
then
    exit 2
fi

if PYTHONPATH="$work_dir/src:$cli_extended_src" PYTHONDONTWRITEBYTECODE=1 \
    "$python_bin" -m pytest \
    "$work_dir/tests/test_endpoints.py::test_reserved_environment_keys_are_rejected_by_plan_and_publish" \
    -q -x -p no:cacheprovider --basetemp="$work_dir/pytest-tmp" \
    >/dev/null 2>&1
then
    echo "canary survived: reserved-key tests did not reject the disabled guard" >&2
    exit 1
fi
echo "canary rejected: reserved-key tests fail when their guard is disabled"
