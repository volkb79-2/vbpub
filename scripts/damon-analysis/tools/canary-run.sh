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
python_bin="${DAMON_ANALYSIS_PYTHON:-python3}"
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
      --exclude=__pycache__ --exclude=.pytest_cache --exclude=venv --exclude=output \
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
  out="$(cd "$work" && PYTHONDONTWRITEBYTECODE=1 "$python_bin" -m pytest "$selector" \
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

echo "damon-analysis gate canaries"

# 1. The flagship bug this project's review found: RCON's req_id is signed
#    on the wire (the auth-failure sentinel is -1); unpacking it as
#    unsigned makes that sentinel decode as 4294967295, so auth failures
#    silently pass through as success.
canary rcon-req-id-unsigned rcon_probe.py \
  "req_id, pkt_type = struct.unpack('<ii', data[:8])" \
  "req_id, pkt_type = struct.unpack('<II', data[:8])" \
  tests/test_rcon_probe.py::TestRecvPacket::test_auth_failure_sentinel_decodes_as_negative_one

# 2. fmt_bytes must not silently return None for values >= 1024 TiB — the
#    exact bug found in this file's own copy of a function three other
#    copies in this toolkit already had a PiB fallback for.
canary fmt-bytes-no-pib-fallback batch_report.py \
  "    return f'{b:.1f} PiB'" \
  "    return None" \
  tests/test_batch_report.py::TestFmtBytes::test_pib_fallback

# 3. A low-rate-but-aged region must classify as 'cold', not stay 'warm'
#    forever — disabling the age check would silently misclassify memory
#    that has gone genuinely cold as still transitional.
canary classifier-ignores-cold-age lib/damon_analysis.py \
  '        elif age_us >= self.cold_age_us:
            return '"'"'cold'"'"'' \
  '        elif False:
            return '"'"'cold'"'"'' \
  tests/test_classifier.py::TestClassify::test_nonzero_rate_below_warm_aged_becomes_cold

echo
if [ "$fail" -gt 0 ]; then
  echo "canaries: $pass rejected, $fail SURVIVED — the gate is not discriminating"
  exit 1
fi
echo "canaries: $pass rejected, 0 survived"
