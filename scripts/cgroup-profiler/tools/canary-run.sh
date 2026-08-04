#!/usr/bin/env bash
# canary-run.sh — prove the gate REJECTS known-bad code.
#
# A suite that is green on correct code proves nothing on its own; it also has
# to go red on incorrect code. Each canary below breaks exactly one contract
# some test claims to assert, and that named test must fail. A canary that
# survives is a hollow oracle, and this script fails when one does.
#
#   tools/canary-run.sh            run every canary
#   tools/canary-run.sh <name>     run one
#
# Each canary runs against a disposable COPY of the tree, so a crash mid-run
# can never leave a sabotaged source file behind. The copy excludes venv/ and
# runs/ (500 MB and growing); the venv is reached through its original path.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
python_bin="${CGPROFILE_PYTHON:-${TESTER_VENV:+$TESTER_VENV/bin/python}}"
python_bin="${python_bin:-$project_dir/venv/bin/python}"
[ -x "$python_bin" ] || { echo "canary: no usable python at $python_bin" >&2; exit 2; }

only="${1:-}"
pass=0 fail=0

# name | file | python-replace-expr | test selector that MUST go red
#
# The sed-free substitution is done in python so the patterns can contain
# anything without shell quoting hazards.
canary() {
  local name="$1" file="$2" find="$3" replace="$4" selector="$5"
  [ -n "$only" ] && [ "$only" != "$name" ] && return 0

  local work
  work="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$work'" RETURN

  tar -C "$project_dir" \
      --exclude=venv --exclude=runs --exclude=output \
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

echo "cgroup-profiler gate canaries"

# 1. The counter-reset rule. A cgroup recreated mid-run resets its counters;
#    reporting the negative delta would put a spike in the chart that never
#    happened. This is the single most repeated invariant in the package.
canary counter-reset-negative lib/util.py \
  '    if cur < prev:
        return None' \
  '    if False:
        return None' \
  tests/test_util.py::TestRate::test_counter_reset_is_unknown_not_negative

# 2. "Absent is not zero." A missing file must read as None, never 0 — the
#    difference between "unlimited" and "zero" is load-bearing everywhere.
canary absent-reads-as-zero lib/util.py \
  '    except (OSError, ValueError):
        return None' \
  '    except (OSError, ValueError):
        return "0"' \
  tests/test_util.py::TestReaders::test_missing_file_is_none_not_zero

# 3. Effective limits must walk the ancestor chain. Reporting only the leaf's
#    own declaration is the exact mistake this tool exists to correct.
canary limits-ignore-ancestors lib/util.py \
  '    present = [v for v in values if v is not None]
    return min(present) if present else None' \
  '    present = [v for v in values if v is not None]
    return present[0] if present else None' \
  tests/test_util.py::TestChainHelpers::test_tightest_ignores_unlimited

# 4. systemd slice names encode their hierarchy. Treating the name as a flat
#    path silently profiles the wrong cgroup — or nothing at all.
canary slice-hierarchy-flattened lib/targets.py \
  '    parts = stem.split("-")' \
  '    parts = [stem]' \
  tests/test_targets.py::TestSliceNaming::test_nested_slice_expands_to_every_ancestor

# 5. Containers that appear after the profiler starts are the whole point of
#    follow mode: a gate spawns its containers mid-run.
canary follow-children-disabled lib/targets.py \
  '            if target.follow_children:' \
  '            if False:' \
  tests/test_targets.py::TestMembership::test_follow_children_picks_up_a_container_that_appears_later

# 6. A run directory with no manifest is a run that cannot be analysed. The
#    naive "add .jsonl if missing" rule rewrote manifest.json and produced
#    exactly that, silently.
canary manifest-renamed-to-jsonl lib/store.py \
  '        return None if os.path.splitext(name)[1] else name' \
  '        return name' \
  tests/test_store.py::test_non_stream_filenames_keep_their_own_extension

# 7. Log-derived phase marks must timestamp from the log line's own RFC3339
#    clock, never from when we happened to read it: container log delivery
#    buffers, sometimes by seconds, and a mark timestamped at arrival time
#    would be systematically late relative to the 250ms sample series it
#    exists to be correlated against — silently wrong on every real run.
canary log-timestamp-uses-arrival-time lib/logtail.py \
  '        when = epoch if epoch is not None else time.time()' \
  '        when = time.time()' \
  tests/test_logtail.py::TestHandleLine::test_literal_match_emits_a_mark_with_the_logs_own_timestamp

echo
if [ "$fail" -gt 0 ]; then
  echo "canaries: $pass rejected, $fail SURVIVED — the gate is not discriminating"
  exit 1
fi
echo "canaries: $pass rejected, 0 survived"
