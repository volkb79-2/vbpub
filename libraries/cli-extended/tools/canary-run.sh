#!/usr/bin/env bash
# Prove that the CLI library suite rejects a disabled output-redaction guard.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${CLI_EXTENDED_PYTHON:-python3}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "canary: no usable Python interpreter at $python_bin" >&2
  exit 2
fi

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
cp -a "$project_dir/src" "$project_dir/tests" "$project_dir/pyproject.toml" "$work_dir/"

if ! "$python_bin" - "$work_dir/src/cli_extended/output.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
before = """                redact_value(value, ())
                if self.debug_raw
                else redact_value(value, self.secrets),"""
after = """                redact_value(value, ())
                if True
                else redact_value(value, self.secrets),"""
if before not in text:
    raise SystemExit("canary target moved; refusing to claim it was tested")
path.write_text(text.replace(before, after, 1))
PY
then
  exit 2
fi

if PYTHONPATH="$work_dir/src" PYTHONDONTWRITEBYTECODE=1 "$python_bin" \
  -m pytest "$work_dir/tests/test_output.py::test_primary_json_and_json_mode_redact_nested_values" \
  -q -x -p no:cacheprovider --basetemp="$work_dir/pytest-tmp" >/dev/null 2>&1
then
  echo "canary survived: JSON redaction test did not reject disabled redaction" >&2
  exit 1
fi
echo "canary rejected: JSON redaction test fails when its guard is disabled"
