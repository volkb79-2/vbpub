#!/usr/bin/env bash
# Resume recently finished Codex runs stopped by a usage limit.
# Usage: resume-usage-limited.sh [--dry-run] [--runs-dir DIR] [--skip RUN]...
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init

usage() {
  echo "usage: $0 [--dry-run] [--runs-dir DIR] [--skip RUN]..." >&2
  exit 2
}

dry_run=0
runs_dir=${CONTROLLER_RUNS_DIR:-$CONTROLLER_HOME/runs}
skip_names=()
read -r -a skip_names <<< "${RESUME_SKIP:-}"
while (($#)); do
  case $1 in
    --dry-run) dry_run=1; shift ;;
    --runs-dir) (($# >= 2)) || usage; runs_dir=$2; shift 2 ;;
    --skip) (($# >= 2)) || usage; skip_names+=("$2"); shift 2 ;;
    *) usage ;;
  esac
done
max_age=${RESUME_MAX_AGE_MINUTES:-180}
if [[ ! $max_age =~ ^[0-9]+$ ]] || (( max_age <= 0 )); then
  echo 'RESUME_MAX_AGE_MINUTES must be a positive integer' >&2
  exit 2
fi
[[ $runs_dir == /* ]] || runs_dir=$PWD/$runs_dir
[[ -d $runs_dir ]] || { echo "runs directory missing: $runs_dir" >&2; exit 2; }
runs_dir=$(cd -- "$runs_dir" && pwd -P)
CONTROLLER_RUNS_DIR=$runs_dir
export CONTROLLER_RUNS_DIR
impl=${CODEX_IMPL_SCRIPT:-$SCRIPT_DIR/codex-impl.sh}
[[ -x $impl ]] || { echo "Codex implementer is not executable: $impl" >&2; exit 2; }

usage_reset_time() {
  python3 - "$1" <<'PY'
import json
import re
import sys

for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get("type") != "turn.failed":
        continue
    error = event.get("error") or {}
    message = error.get("message", "") if isinstance(error, dict) else str(error)
    if not re.search(r"hit your usage limit", message, re.IGNORECASE):
        continue
    reset = re.search(r"try again at\s+(\d{1,2}:\d{2}(?:\s*[AP]M)?)", message, re.IGNORECASE)
    print(reset.group(1).strip() if reset else "unknown")
    raise SystemExit(0)
raise SystemExit(1)
PY
}

is_skipped() {
  local candidate
  for candidate in "${skip_names[@]}"; do
    [[ $candidate == "$1" ]] && return 0
  done
  return 1
}

thread_id_from_stream() {
  python3 - "$1" <<'PY'
import json
import sys
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get("type") == "thread.started" and event.get("thread_id"):
        print(event["thread_id"])
        break
PY
}

failed=0
while IFS= read -r -d '' run_file; do
  run=${run_file##*/}
  run=${run%.jsonl}
  [[ $run =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $run != *..* ]] || { echo "SKIP invalid run name: $run"; continue; }
  case $run in *-resumed) echo "SKIP $run (already a resumed run)"; continue ;; esac
  if is_skipped "$run"; then echo "SKIP $run (RESUME_SKIP)"; continue; fi
  if [[ ! -f $runs_dir/$run.done ]]; then echo "SKIP $run (no completion marker)"; continue; fi
  done_status=$(head -n 1 "$runs_dir/$run.done")
  if [[ $done_status =~ ^EXIT=0([[:space:]]|$) ]]; then echo "SKIP $run (EXIT=0)"; continue; fi
  if [[ -e $runs_dir/$run-resumed.done ]]; then echo "SKIP $run (resume attempt already recorded)"; continue; fi
  if ! reset_time=$(usage_reset_time "$run_file"); then continue; fi
  if [[ ! -f $runs_dir/$run.worktree ]]; then echo "SKIP $run (no worktree sidecar)"; continue; fi
  IFS= read -r worktree < "$runs_dir/$run.worktree" || true
  worktree=$(realpath -e -- "$worktree" 2>/dev/null || true)
  if [[ -z $worktree || ! -d $worktree ]]; then echo "SKIP $run (worktree is unavailable)"; continue; fi
  if [[ ! -f $runs_dir/$run.route ]]; then echo "SKIP $run (no route sidecar)"; continue; fi
  IFS= read -r route < "$runs_dir/$run.route" || true
  case $route in luna|deepseek) ;; *) echo "SKIP $run (invalid route sidecar)"; continue ;; esac
  thread_id=$(thread_id_from_stream "$run_file")
  if [[ -z $thread_id ]]; then echo "SKIP $run (no thread id)"; continue; fi
  printf 'RESUME %s thread=%s reset=%s worktree=%s\n' "$run" "$thread_id" "$reset_time" "$worktree"
  if (( dry_run )); then continue; fi
  if CODEX_ROUTE=$route "$impl" "$worktree" "$SCRIPT_DIR/finisher-generic.md" "$run-resumed" resume "$thread_id"; then
    :
  else
    failed=1
  fi
done < <(find "$runs_dir" -maxdepth 1 -type f -name '*.jsonl' -mmin "-$max_age" -print0 | sort -z)
exit "$failed"
