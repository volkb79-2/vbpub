#!/usr/bin/env bash
# canary-run.sh — prove the gate REJECTS known-bad code.
#
# A suite that is green on correct code proves nothing on its own; it also
# has to go red on incorrect code. Each canary below breaks exactly one
# contract some test claims to assert, and that named test must fail. A
# canary that survives is a hollow oracle, and this script fails when one
# does. Pattern matches scripts/cgroup-profiler/tools/canary-run.sh.
#
#   tools/canary-run.sh            run every canary
#   tools/canary-run.sh <name>     run one
#
# Each canary runs against a disposable COPY of the tree, so a crash
# mid-run can never leave a sabotaged source file behind.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
python_bin="${DEBIAN_INSTALL_V2_PYTHON:-python3}"
command -v "$python_bin" >/dev/null 2>&1 || { echo "canary: no usable python at $python_bin" >&2; exit 2; }

only="${1:-}"
pass=0 fail=0

# name | file | python-replace-expr | test selector that MUST go red
canary() {
  local name="$1" file="$2" find="$3" replace="$4" selector="$5"
  [ -n "$only" ] && [ "$only" != "$name" ] && return 0

  local work
  work="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$work'" RETURN

  tar -C "$project_dir" \
      --exclude=__pycache__ --exclude=.pytest_cache \
      -cf - . | tar -C "$work" -xf -

  if ! "$python_bin" - "$work/$file" "$find" "$replace" <<'PY'
import sys, pathlib
path, find, replace = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text()
if find not in text:
    sys.exit(f"canary target not found in {path.name}: {find!r}")
path.write_text(text.replace(find, replace, 1))
PY
  then
    printf '  %-34s BROKEN CANARY (target text not found — the code moved)\n' "$name"
    fail=$((fail + 1))
    return 0
  fi

  local out rc
  set +e
  out="$(cd "$work" && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 "$python_bin" -m pytest "$selector" \
          -q -x -p no:cacheprovider --basetemp="$work/.pt" 2>&1)"
  rc=$?
  set -e

  if [ "$rc" -ne 0 ]; then
    printf '  %-34s ok (gate rejected it)\n' "$name"
    pass=$((pass + 1))
  else
    printf '  %-34s SURVIVED — %s does not actually assert this\n' "$name" "$selector"
    printf '%s\n' "$out" | tail -5 | sed 's/^/      /'
    fail=$((fail + 1))
  fi
}

echo "debian-install-v2 gate canaries"

# 1. Obsolete v1 config names must be rejected, not silently accepted — the
#    entire point of the v2 clean break (README: "rejected rather than
#    silently ignoring").
canary obsolete-v1-name-accepted debian_install_v2/config.py \
  '    if obsolete:
        names = ", ".join(obsolete)
        raise ConfigError(f"obsolete v1 setting(s) rejected by v2 clean break: {names}")' \
  '    if False:
        names = ", ".join(obsolete)
        raise ConfigError(f"obsolete v1 setting(s) rejected by v2 clean break: {names}")' \
  debian_install_v2/tests/test_config.py::test_obsolete_v1_names_are_rejected

# 2. Dry run must never execute a real command — README: "Dry run records
#    every privileged action... it does not execute commands." A dry-run
#    that silently starts running real subprocess calls on a fresh-install
#    target is the single most dangerous regression this project can have.
canary dry-run-executes-anyway debian_install_v2/actions.py \
  '        if self.dry_run:
            return None' \
  '        if False:
            return None' \
  debian_install_v2/tests/test_actions.py::test_dry_run_records_but_does_not_execute

# 3. The command allowlist is the one gate between config-driven install
#    logic and arbitrary host command execution. Disabling it silently
#    would let any executable through.
canary command-allowlist-disabled debian_install_v2/actions.py \
  '        if command not in _SAFE_COMMANDS:
            raise ActionError(f"executable is not on the action allowlist: {command}")' \
  '        if False:
            raise ActionError(f"executable is not on the action allowlist: {command}")' \
  debian_install_v2/tests/test_actions.py::test_unallowlisted_executable_and_option_are_refused

echo
if [ "$fail" -gt 0 ]; then
  echo "canaries: $pass rejected, $fail SURVIVED — the gate is not discriminating"
  exit 1
fi
echo "canaries: $pass rejected, 0 survived"
