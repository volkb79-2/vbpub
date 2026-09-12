#!/usr/bin/env bash
# canary-run.sh — prove the assay-r1 lane REJECTS known-bad code (RG-55 C2,
# assay-r3).
#
# A suite that is green on correct code proves nothing on its own; it also
# has to go red on incorrect code. Each canary below breaks exactly one
# invariant a named test provably asserts, and that test must fail. A
# canary that survives is a hollow oracle, and this script fails when one
# does.
#
#   tools/canary-run.sh            run every canary
#   tools/canary-run.sh <name>     run one
#
# Shape and mechanics mirror scripts/cgroup-profiler/tools/canary-run.sh
# (same repo, same r1/r2/r3 naming, built and proven first there): each
# canary runs against a disposable tar COPY of the tree (never the real
# source — a crash mid-run can never leave a sabotaged file behind, and
# there is nothing to "restore"), and asserts the ONE test selector that
# the assay-r1 lane's own test command (`assay.toml [lanes.r1]`'s argv,
# `pytest tests -q --cov=. ...`) would run as part of judging this project.
# Read this as a decision, not an oversight: assay-r1's literal argv runs
# the whole 830+-test suite, and re-running all of it in a scratch copy
# for every canary would cost minutes per canary on a HOST LOAD budget
# (run-gate.toml §6/SPEC.md) shared with a production game server, for no
# more signal than the one selector that actually encodes the invariant —
# the same reasoning scripts/cgroup-profiler/tools/canary-run.sh already
# applied. A failing selector here means pytest exits non-zero, which is
# exactly what makes assay's own R0 (let alone R1) FAIL for the real
# assay-r1 lane, so the causal claim ("the gate would have caught this")
# holds without paying to re-run the full suite.
#
# The sed-free substitution is done in python so the patterns can contain
# anything without shell quoting hazards.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
python_bin="${RUN_GATE_CANARY_PYTHON:-python3}"
command -v "$python_bin" >/dev/null 2>&1 || {
  echo "canary: no usable python ($python_bin) -- set RUN_GATE_CANARY_PYTHON" >&2
  exit 2
}

only="${1:-}"
pass=0 fail=0

# name | file (relative to project_dir) | find | replace | test selector that MUST go red
canary() {
  local name="$1" file="$2" find="$3" replace="$4" selector="$5"
  [ -n "$only" ] && [ "$only" != "$name" ] && return 0

  local work
  work="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$work'" RETURN

  # Excludes are speed-only (a stale coverage/state directory carried into
  # the copy changes nothing about which test fails): tools/assay/ is a 2
  # MiB pinned zipapp pytest never imports, nyxloom-trove/ is reports/docs,
  # the rest is generated state a fresh copy does not need. `.assay/` (S9a,
  # round-1 review) is NOT speed-only to exclude: a mutation lane can be
  # live-appending `.assay/progress-*.jsonl` while this script runs (assay-r2
  # is bare-host, hours long) -- `tar` exits 1 on "file changed as we read
  # it" and this script runs under `set -euo pipefail`, so copying it risked
  # a spurious canary failure racing a real, unrelated mutation sweep.
  tar -C "$project_dir" \
      --exclude=nyxloom-trove --exclude=tools/assay --exclude=.assay \
      --exclude=__pycache__ --exclude=.pytest_cache --exclude=.hypothesis \
      --exclude=.run-gate --exclude=.coverage --exclude=coverage.json \
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
    printf '  %-34s BROKEN CANARY (target text not found -- the code moved)\n' "$name"
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
    printf '  %-34s ok (assay-r1 would reject it)\n' "$name"
    pass=$((pass + 1))
  else
    printf '  %-34s SURVIVED -- %s does not actually assert this\n' "$name" "$selector"
    printf '%s\n' "$out" | tail -5 | sed 's/^/      /'
    fail=$((fail + 1))
  fi
}

echo "run-gate-project assay-r1 canary"

# The RG-27 trap this whole history-series design exists to avoid: one slow
# outlier read as a lane's typical cost. duration_stats's own docstring says
# it plainly ("MEDIAN, not mean, and that is the point"). Flipping the
# odd-length branch's nearest-element median to an arithmetic mean must turn
# (10, 10, 100)'s reported "typical cost" from 10.0 into 40.0 --
# TestHistoryRollingSeries::test_one_slow_outlier_does_not_become_the_typical_cost
# asserts exactly 10.0 and must fail.
#
# S9b (round-1 review): the median-computing block below is BYTE-IDENTICAL
# in duration_stats and series_stats (run-gate.py), so `find` is widened to
# include each function's own distinct empty-input `return` line as an
# anchor -- `text.replace(find, replace, 1)` would otherwise silently
# target whichever copy appears FIRST in the file (duration_stats,
# correct today only by position), leaving series_stats' own median
# un-canaried. Two canaries now, one per function, each unambiguous.
canary median-not-mean run-gate.py \
  '                "max_seconds": None}
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else \
        round((values[mid - 1] + values[mid]) / 2, 3)' \
  '                "max_seconds": None}
    mid = len(values) // 2
    median = round(sum(values) / len(values), 3)' \
  tests/test_run_gate.py::TestHistoryRollingSeries::test_one_slow_outlier_does_not_become_the_typical_cost

canary median-not-mean-series-stats run-gate.py \
  '        return {"count": 0, "min": None, "median": None, "max": None}
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else \
        round((values[mid - 1] + values[mid]) / 2, 3)' \
  '        return {"count": 0, "min": None, "median": None, "max": None}
    mid = len(values) // 2
    median = round(sum(values) / len(values), 3)' \
  tests/test_run_gate.py::TestHistoryResourceSeries::test_median_resists_a_10x_outlier_and_absent_entries_are_excluded

echo
if [ "$fail" -gt 0 ]; then
  echo "canary: $pass rejected, $fail SURVIVED -- the gate is not discriminating"
  exit 1
fi
echo "canary: $pass rejected, 0 survived"
