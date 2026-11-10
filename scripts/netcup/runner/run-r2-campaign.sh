#!/usr/bin/env bash
# Description: supervise a native Assay R2 campaign across budget exits.
# usage: run-r2-campaign.sh <campaign>-s<N> <checkout-commit> <project-dir> <lane> [run-gate args...]
#        run-r2-campaign.sh stop <campaign>
# Use the same -s<N> suffix for run-job and this campaign start. Evidence is
# stored per start while the lock and Assay resume state stay with the campaign.
set -Eeuo pipefail
umask 077
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check

name_re='^[A-Za-z0-9][A-Za-z0-9._-]*$'
start_re='^([A-Za-z0-9][A-Za-z0-9._-]*)-s([1-9][0-9]*)$'

usage() {
  echo "usage: $0 <campaign>-s<N> <checkout-commit> <project-dir> <lane> [run-gate args...]" >&2
  echo "       $0 stop <campaign>" >&2
  exit 2
}

split_campaign_name() {
  local value=$1
  [[ $value =~ $name_re ]] || { echo 'invalid campaign name' >&2; return 2; }
  if [[ $value =~ $start_re ]]; then
    campaign_base=${BASH_REMATCH[1]}
    campaign_start=$value
  else
    campaign_base=$value
    campaign_start=
  fi
}

proc_identity() {
  local stat rest
  local -a fields
  stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
  rest=${stat##*) }
  read -r -a fields <<< "$rest"
  (( ${#fields[@]} >= 20 )) || return 1
  printf '%s %s\n' "${fields[0]}" "${fields[19]}"
}

pid_running() {
  local identity state
  [[ $1 =~ ^[1-9][0-9]*$ && -d /proc/$1 ]] || return 1
  identity=$(proc_identity "$1") || return 1
  state=${identity%% *}
  [[ $state != Z ]]
}

pid_matches_start() {
  local identity
  [[ $1 =~ ^[1-9][0-9]*$ && $2 =~ ^[0-9]+$ ]] || return 1
  identity=$(proc_identity "$1") || return 1
  [[ ${identity#* } == "$2" ]]
}

wait_external_pid() {
  local pid=$1 expected_start=$2 i
  for ((i = 0; i < 50; i++)); do
    pid_matches_start "$pid" "$expected_start" || return 0
    pid_running "$pid" || return 0
    sleep 0.1
  done
  if pid_matches_start "$pid" "$expected_start" && pid_running "$pid"; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  ! pid_matches_start "$pid" "$expected_start" || ! pid_running "$pid"
}

remove_lane_containers() {
  local start=$1 names container rc=0
  command -v docker >/dev/null || {
    echo 'stop: docker is unavailable; the run-gate client was still signalled' >&2
    return 1
  }
  names=$(docker ps -a \
    --filter "label=run-gate.job=$start" \
    --filter "label=run-gate.campaign=$start" \
    --format '{{.Names}}') || {
      echo 'stop: could not list the campaign lane containers' >&2
      return 1
    }
  while IFS= read -r container; do
    [[ -n $container ]] || continue
    [[ $container =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || {
      echo "stop: refusing unexpected Docker container name: $container" >&2
      rc=1
      continue
    }
    docker rm -f "$container" >/dev/null || {
      echo "stop: could not remove exact lane container $container" >&2
      rc=1
    }
  done <<< "$names"
  return "$rc"
}

read_active() {
  local file=$1 key value
  active_start='' active_supervisor='' active_supervisor_start=''
  active_lane='' active_lane_start='' active_sampler='' active_sampler_start=''
  [[ -f $file && ! -L $file ]] || return 1
  while IFS='=' read -r key value; do
    case "$key" in
      start) active_start=$value ;;
      supervisor) active_supervisor=$value ;;
      supervisor_start) active_supervisor_start=$value ;;
      lane) active_lane=$value ;;
      lane_start) active_lane_start=$value ;;
      sampler) active_sampler=$value ;;
      sampler_start) active_sampler_start=$value ;;
      '') ;;
      *) echo "stop: invalid active campaign record: $file" >&2; return 2 ;;
    esac
  done < "$file"
  [[ $active_start =~ $start_re ]] || {
    echo "stop: invalid active campaign start in $file" >&2
    return 2
  }
  [[ $active_supervisor =~ ^[1-9][0-9]*$ ]] || {
    echo "stop: invalid supervisor pid in $file" >&2
    return 2
  }
  [[ $active_supervisor_start =~ ^[0-9]+$ ]] || {
    echo "stop: invalid supervisor start time in $file" >&2
    return 2
  }
  [[ -z $active_lane || $active_lane =~ ^[1-9][0-9]*$ ]] || {
    echo "stop: invalid lane pid in $file" >&2
    return 2
  }
  [[ -z $active_lane || $active_lane_start =~ ^[0-9]+$ ]] || {
    echo "stop: invalid lane start time in $file" >&2
    return 2
  }
  [[ -z $active_sampler || $active_sampler =~ ^[1-9][0-9]*$ ]] || {
    echo "stop: invalid sampler pid in $file" >&2
    return 2
  }
  [[ -z $active_sampler || $active_sampler_start =~ ^[0-9]+$ ]] || {
    echo "stop: invalid sampler start time in $file" >&2
    return 2
  }
}

stop_campaign() {
  local requested=$1 lock_file active_file cmd acquired=0
  split_campaign_name "$requested" || return $?
  lock_file="$RUNNER_HOME/locks/$campaign_base.lock"
  active_file="$RUNNER_HOME/locks/$campaign_base.active"
  install -d -m 0700 "$RUNNER_HOME/locks"
  exec 9>"$lock_file"
  if flock -n 9; then
    acquired=1
    if [[ ! -e $active_file ]]; then
      echo "campaign $campaign_base is not running" >&2
      flock -u 9
      return 1
    fi
  fi
  if ! read_active "$active_file"; then
    if (( acquired )); then flock -u 9; fi
    echo "stop: campaign lock is held without a readable active record: $campaign_base" >&2
    return 2
  fi
  [[ -z $campaign_start || $campaign_start == "$active_start" ]] || {
    if (( acquired )); then flock -u 9; fi
    echo "campaign $requested is not the active start ($active_start)" >&2
    return 1
  }
  if (( ! acquired )) && pid_running "$active_supervisor"; then
    pid_matches_start "$active_supervisor" "$active_supervisor_start" || {
      echo "stop: active supervisor pid $active_supervisor was reused" >&2
      return 2
    }
    cmd=$(tr '\0' ' ' < "/proc/$active_supervisor/cmdline" 2>/dev/null || true)
    [[ $cmd == *run-r2-campaign.sh* && $cmd == *"$active_start"* ]] || {
      echo "stop: active supervisor pid $active_supervisor does not match $active_start" >&2
      return 2
    }
    kill -TERM "$active_supervisor" 2>/dev/null || true
    wait_external_pid "$active_supervisor" "$active_supervisor_start" || {
      echo "stop: supervisor $active_supervisor did not exit" >&2
      return 1
    }
  fi

  # A supervisor killed outside its trap may leave children and its control
  # record behind. PIDs and Docker labels are scoped to this unique start.
  if [[ -n $active_lane ]] && pid_matches_start "$active_lane" "$active_lane_start" \
    && pid_running "$active_lane"; then
    kill -TERM "$active_lane" 2>/dev/null || true
    wait_external_pid "$active_lane" "$active_lane_start" || {
      echo "stop: lane client $active_lane did not exit" >&2
      return 1
    }
  fi
  if [[ -n $active_sampler ]] && pid_matches_start "$active_sampler" "$active_sampler_start" \
    && pid_running "$active_sampler"; then
    kill -TERM "$active_sampler" 2>/dev/null || true
    wait_external_pid "$active_sampler" "$active_sampler_start" || {
      echo "stop: sampler $active_sampler did not exit" >&2
      return 1
    }
  fi
  remove_lane_containers "$active_start" || return 1

  if (( acquired )); then
    rm -f -- "$active_file"
    flock -u 9
  else
    local i
    for ((i = 0; i < 50; i++)); do
      if flock -n 9; then
        rm -f -- "$active_file"
        flock -u 9
        echo "stopped campaign $active_start; campaign lock released"
        return 0
      fi
      sleep 0.1
    done
    echo "stop: campaign lock is still held: $campaign_base" >&2
    return 1
  fi
  echo "stopped stale campaign $active_start; campaign lock released"
}

if [[ ${1:-} == stop ]]; then
  [[ $# == 2 ]] || usage
  stop_campaign "$2"
  exit $?
fi

[[ $# -ge 4 ]] || usage
start=$1; commit=$2; rel=$3; lane=$4; shift 4
[[ $start =~ $start_re ]] || {
  echo 'campaign start name must end in a positive -s<N> suffix' >&2
  exit 2
}
campaign_base=${BASH_REMATCH[1]}
[[ $commit =~ ^[0-9a-f]{40,64}$ ]] || { echo 'checkout must be a full commit id' >&2; exit 2; }
[[ $rel != /* && $rel != *..* ]] || { echo 'project dir must be relative to the checkout' >&2; exit 2; }
max_attempts=${R2_MAX_ATTEMPTS:-12}
[[ $max_attempts =~ ^[1-9][0-9]*$ ]] || { echo 'R2_MAX_ATTEMPTS must be a positive decimal integer' >&2; exit 2; }

checkout="$RUNNER_HOME/checkouts/$commit"
[[ -d $checkout && ! -L $checkout ]] || {
  echo "persistent checkout is missing or not a directory: $checkout" >&2
  exit 2
}
dirty=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || {
  echo "could not read persistent checkout status: $checkout" >&2
  exit 2
}
if [[ -n $dirty ]]; then
  echo "persistent checkout is dirty; commit or remove changes before starting R2: $checkout" >&2
  printf '%s\n' "$dirty" >&2
  exit 2
fi
[[ -d $checkout/$rel ]] || { echo "project directory is missing from the persistent checkout: $rel" >&2; exit 2; }

clamp_jobs() {  # DST-37: never pass --jobs above the lane's judge.mutation.jobs_max
  local toml=$checkout/$rel/assay.toml i max
  [[ -f $toml ]] || return 0
  max=$(python3 - "$toml" "$lane" <<'PY'
import sys, tomllib
cfg = tomllib.load(open(sys.argv[1], "rb"))
mut = cfg.get("lanes", {}).get(sys.argv[2], {}).get("judge", {}).get("mutation", {})
print(mut.get("jobs_max", mut.get("jobs", "")))
PY
  ) || return 0
  [[ $max =~ ^[0-9]+$ ]] || return 0
  for i in "${!args[@]}"; do
    if [[ ${args[i]} == --jobs && ${args[i+1]:-} =~ ^[0-9]+$ ]] && (( args[i+1] > max )); then
      echo "clamping --jobs ${args[i+1]} -> $max (lane $lane jobs_max)" >&2; args[i+1]=$max
    elif [[ ${args[i]} =~ ^--jobs=([0-9]+)$ ]] && (( BASH_REMATCH[1] > max )); then
      echo "clamping ${args[i]} -> --jobs=$max (lane $lane jobs_max)" >&2; args[i]="--jobs=$max"
    fi
  done
}

args=("$@"); clamp_jobs
dir="$RUNNER_HOME/campaigns/$start"
control_file="$RUNNER_HOME/locks/$campaign_base.active"
install -d -m 0700 "$RUNNER_HOME/campaigns" "$RUNNER_HOME/locks"
exec 9>"$RUNNER_HOME/locks/$campaign_base.lock"
flock -n 9 || { echo "campaign $campaign_base is already supervised" >&2; exit 73; }
mkdir -m 0700 -- "$dir" || { echo "campaign start already exists: $dir" >&2; exit 3; }

progress_dir="$checkout/$rel/.assay"
mkdir -p -- "$progress_dir"
rm -f -- "$dir/final-exit" "$dir/campaign-summary.json" "$dir/verdict.json"
printf '%s\n' "$(date -u +%FT%TZ)" > "$dir/.supervision-start"
printf 'started_utc=%s\nstart=%s\ncommit=%s\nproject=%s\nlane=%s\nargs=%s\n' \
  "$(date -u +%FT%TZ)" "$start" "$commit" "$rel" "$lane" "${args[*]}" > "$dir/metadata.txt"

lane_pid=
sampler_pid=
attempt=0
stalled=0
finalized=false
cancel_requested=false

write_active() {
  local tmp="$control_file.$$" supervisor_identity
  local record_lane=$lane_pid record_sampler=$sampler_pid
  local lane_identity='' sampler_identity=''
  supervisor_identity=$(proc_identity "$$")
  if [[ -n $record_lane ]]; then
    lane_identity=$(proc_identity "$record_lane" || true)
    [[ -n $lane_identity ]] || record_lane=''
  fi
  if [[ -n $record_sampler ]]; then
    sampler_identity=$(proc_identity "$record_sampler" || true)
    [[ -n $sampler_identity ]] || record_sampler=''
  fi
  printf 'start=%s\nsupervisor=%s\nsupervisor_start=%s\nlane=%s\nlane_start=%s\nsampler=%s\nsampler_start=%s\n' \
    "$start" "$$" "${supervisor_identity#* }" "$record_lane" \
    "${lane_identity#* }" "$record_sampler" "${sampler_identity#* }" > "$tmp"
  mv -f -- "$tmp" "$control_file"
}
write_active

candidate_count() {
  python3 - "$progress_dir" <<'PY'
import json
from pathlib import Path
import sys

count = 0
for path in Path(sys.argv[1]).glob("progress-*.jsonl"):
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("event") == "candidate":
                    count += 1
    except OSError:
        continue
print(count)
PY
}

sampler_loop() {
  local sleep_pid=
  trap '[[ -z $sleep_pid ]] || kill "$sleep_pid" 2>/dev/null || true; exit 0' TERM INT
  while :; do
    printf '%s,%s,%s,%s\n' "$(date -u +%FT%TZ)" \
      "$(cat /sys/fs/cgroup/dev.slice/"$GATES_SLICE"/memory.current 2>/dev/null || echo 0)" \
      "$(awk '/^some/ {sub("avg10=","",$2); print $2}' /proc/pressure/cpu)" \
      "$(awk '/^some/ {sub("avg10=","",$2); print $2}' /proc/pressure/memory)" >> "$dir/samples.csv"
    sleep 10 &
    sleep_pid=$!
    wait "$sleep_pid" || true
    sleep_pid=
  done
}

stop_children() {
  local state i had_lane=false
  if [[ -n $lane_pid ]] && pid_running "$lane_pid"; then
    had_lane=true
    kill -TERM "$lane_pid" 2>/dev/null || true
    for ((i = 0; i < 50; i++)); do
      state=$(awk '{print $3}' "/proc/$lane_pid/stat" 2>/dev/null || true)
      [[ -z $state || $state == Z ]] && break
      sleep 0.1
    done
    if pid_running "$lane_pid"; then kill -KILL "$lane_pid" 2>/dev/null || true; fi
    wait "$lane_pid" 2>/dev/null || true
    lane_pid=
  fi
  if [[ $cancel_requested == true || $had_lane == true ]]; then
    remove_lane_containers "$start" || true
  fi
  if [[ -n $sampler_pid ]] && pid_running "$sampler_pid"; then
    kill -TERM "$sampler_pid" 2>/dev/null || true
    for ((i = 0; i < 50; i++)); do
      state=$(awk '{print $3}' "/proc/$sampler_pid/stat" 2>/dev/null || true)
      [[ -z $state || $state == Z ]] && break
      sleep 0.1
    done
    if pid_running "$sampler_pid"; then kill -KILL "$sampler_pid" 2>/dev/null || true; fi
    wait "$sampler_pid" 2>/dev/null || true
    sampler_pid=
  fi
}

write_final() {
  local exit_code=$1 reason=$2 stop_reason=$3 result finished
  [[ $finalized == false ]] || return 0
  finalized=true
  result=NOT_PASS
  [[ $exit_code != 0 ]] || result=PASS
  finished=$(date -u +%FT%TZ)
  printf '%s\n' "$exit_code" > "$dir/final-exit.tmp"
  mv -f -- "$dir/final-exit.tmp" "$dir/final-exit"
  python3 - "$start" "$campaign_base" "$commit" "$rel" "$lane" "$attempt" \
    "$exit_code" "$result" "$reason" "$stop_reason" "$finished" \
    > "$dir/campaign-summary.json.tmp" <<'PY'
import json
import sys

(campaign, base, commit, project, lane, attempts, final_exit,
 result, reason, stop_reason, finished) = sys.argv[1:]
summary = {
    "campaign": campaign,
    "campaign_base": base,
    "commit": commit,
    "project": project,
    "lane": lane,
    "attempts": int(attempts),
    "final_exit": int(final_exit),
    "result": result,
    "reason": reason,
    "stop_reason": stop_reason or None,
    "finished_utc": finished,
}
print(json.dumps(summary, separators=(",", ":")))
PY
  mv -f -- "$dir/campaign-summary.json.tmp" "$dir/campaign-summary.json"
  printf 'finished_utc=%s\nfinal_exit=%s\nreason=%s\n' \
    "$finished" "$exit_code" "$reason" >> "$dir/metadata.txt"
}

cleanup() {
  local rc=$?
  trap - EXIT TERM INT
  stop_children
  if [[ -f $control_file && ! -L $control_file ]]; then
    local current
    current=$(sed -n 's/^start=//p' "$control_file")
    [[ $current != "$start" ]] || rm -f -- "$control_file"
  fi
  exit "$rc"
}

on_signal() {
  local signal=$1 exit_code=143
  [[ $signal != INT ]] || exit_code=130
  trap - TERM INT
  cancel_requested=true
  printf '%s signal=%s attempt=%s\n' "$(date -u +%FT%TZ)" "$signal" "$attempt" >> "$dir/attempts.log"
  stop_children
  cancel_requested=false
  write_final "$exit_code" cancelled "$signal"
  exit "$exit_code"
}

trap cleanup EXIT
trap 'on_signal TERM' TERM
trap 'on_signal INT' INT

sampler_loop &
sampler_pid=$!
write_active

identity_check
while :; do
  attempt=$((attempt + 1))
  before=$(candidate_count)
  attempt_name=$(printf 'attempt-%02d' "$attempt")
  RUN_GATE_JOB=$start RUN_GATE_CAMPAIGN=$start \
    bash "$here/run-lane.sh" "$commit" "$rel" "$lane" "${args[@]}" \
      > "$dir/$attempt_name.log" 2>&1 &
  lane_pid=$!
  printf '%s\n' "$lane_pid" > "$dir/lane.pid"
  write_active
  set +e
  wait "$lane_pid"
  rc=$?
  set -e
  lane_pid=
  rm -f -- "$dir/lane.pid"
  printf '%s\n' "$rc" > "$dir/$attempt_name.exit"
  after=$(candidate_count)
  printf '%s attempt=%s exit=%s completed_candidates=%s->%s\n' \
    "$(date -u +%FT%TZ)" "$attempt" "$rc" "$before" "$after" >> "$dir/attempts.log"
  if [[ $rc != 4 ]]; then
    final=$rc
    reason='lane-exit'
    stop_reason=
    [[ $final != 0 ]] || reason=pass
    break
  fi
  if (( after <= before )); then stalled=$((stalled + 1)); else stalled=0; fi
  if (( stalled >= 2 )); then
    final=4
    reason=budget
    stop_reason=stalled
    printf 'stopping: stalled=%s attempt=%s reason=%s\n' "$stalled" "$attempt" "$stop_reason" >> "$dir/attempts.log"
    break
  fi
  if (( attempt >= max_attempts )); then
    final=4
    reason=budget
    stop_reason='max-attempts'
    printf 'stopping: stalled=%s attempt=%s reason=%s\n' "$stalled" "$attempt" "$stop_reason" >> "$dir/attempts.log"
    break
  fi
done
write_final "$final" "$reason" "$stop_reason"

resolve_assay_lane() {  # run-gate lane -> Assay lane from the committed lane declaration
  python3 - "$checkout" "$rel" "$lane" <<'PY' 2>/dev/null || echo "$lane"
import sys, tomllib
root, rel, lane = sys.argv[1:4]
for path in (f"{root}/{rel}/run-gate.toml", f"{root}/run-gate.root.toml"):
    try:
        decl = tomllib.load(open(path, "rb")).get("lanes", {}).get(lane)
    except (OSError, tomllib.TOMLDecodeError):
        continue
    if decl is not None:
        print(decl.get("assay_lane", lane))
        break
else:
    print(lane)
PY
}

verdict_src=$progress_dir/verdict-$(resolve_assay_lane).json
if [[ -f $verdict_src && $verdict_src -nt $dir/.supervision-start ]]; then
  cp -- "$verdict_src" "$dir/verdict.json"
elif [[ $final == 0 ]]; then
  echo "campaign $start finished with exit 0 but the lane wrote no fresh Assay verdict at $verdict_src; refusing to report success" >&2
  printf 'verdict_missing=%s\n' "$verdict_src" >> "$dir/metadata.txt"
  exit 70
else
  printf 'verdict_missing=%s\n' "$verdict_src" >> "$dir/metadata.txt"
fi
exit "$final"
