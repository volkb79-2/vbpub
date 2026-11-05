#!/usr/bin/env bash
# Description: time-boxed native R2 throughput comparison: run `run-gate <lane> --jobs N` on fresh state for WINDOW seconds, stop its exact lane container, record progress.
# usage: run-jobs-window.sh <commit> <project-dir> <lane> <result-name> <window-seconds> [jobs-list, default "1 3 6 auto"]
set -Eeuo pipefail
umask 077
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
commit=$1; rel=$2; lane=$3; name=$4; window=$5; list=${6:-"1 3 6 auto"}
base=$RUNNER_HOME/campaigns/$name
install -d -m 0700 "$base"
co=$RUNNER_HOME/checkouts/$commit
state=$co/.run-gate/assay-state/$rel
assay=$co/$rel/.assay
printf 'commit=%s project=%s lane=%s window=%ss list=%s start=%s\n' "$commit" "$rel" "$lane" "$window" "$list" "$(date -u +%FT%TZ)" >> "$base/metadata.txt"
printf 'jobs,lane_exit,wall_s,peak_gates_mem_bytes,max_cpu_psi,max_mem_psi,candidates_done\n' > "$base/summary.csv"
for n in $list; do
  identity_check
  install -d -m 0700 "$base/old-$n"
  if [[ -e $state ]]; then mv "$state" "$base/old-$n/state.$(date +%s)"; fi
  if [[ -e $assay ]]; then mv "$assay" "$base/old-$n/assay.$(date +%s)"; fi
  mkdir -p "$state" "$assay"
  run=$base/jobs-$n; install -d -m 0700 "$run"; : > "$run/samples.csv"
  ( while :; do
      printf '%s,%s,%s,%s\n' "$(date -u +%FT%TZ)" "$(cat /sys/fs/cgroup/dev.slice/"$GATES_SLICE"/memory.current 2>/dev/null || echo 0)" \
        "$(awk '/^some/ {sub("avg10=","",$2); print $2}' /proc/pressure/cpu)" "$(awk '/^some/ {sub("avg10=","",$2); print $2}' /proc/pressure/memory)" >> "$run/samples.csv"
      sleep 2
    done ) &
  sampler=$!
  t0=$(date +%s.%N)
  bash "$here/run-lane.sh" "$commit" "$rel" "$lane" --jobs "$n" > "$run/run.log" 2>&1 &
  lane_pid=$!
  # the window starts when the first candidate event appears, so the baseline (R0/R1) is excluded
  started=0; waited=0
  while kill -0 "$lane_pid" 2>/dev/null; do
    if (( started == 0 )) && grep -qs '"event":"candidate"\|"candidate_index":0' "$assay"/progress-*.jsonl 2>/dev/null; then started=$(date +%s); fi
    if (( started > 0 )) && (( $(date +%s) - started >= window )); then break; fi
    waited=$((waited + 5)); (( waited > 7200 )) && break
    sleep 5
  done
  if kill -0 "$lane_pid" 2>/dev/null; then
    # RG-242: label first (new names carry no commit-prefix), then the pre-RG-242 `run-gate-<sha>-` name
    cn=$(docker ps --filter "label=run-gate.commit=$commit" --format '{{.Names}}' | head -1 || true)
    [[ -n $cn ]] || cn=$(docker ps --format '{{.Names}}' | grep "^run-gate-$commit-" | head -1 || true)
    [[ -n $cn ]] && docker stop "$cn" >/dev/null
  fi
  set +e; wait "$lane_pid"; rc=$?; set -e
  t1=$(date +%s.%N)
  kill "$sampler" 2>/dev/null || true
  wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')
  peak=$(awk -F, '$2>m {m=$2} END{print m+0}' "$run/samples.csv")
  mcpu=$(awk -F, '$3>m {m=$3} END{print m+0}' "$run/samples.csv")
  mmem=$(awk -F, '$4>m {m=$4} END{print m+0}' "$run/samples.csv")
  mkdir -p "$run/assay-out"; cp -a "$assay/." "$run/assay-out/" || true
  done_n=$(cat "$assay"/progress-*.jsonl 2>/dev/null | grep -c '"event":"candidate"' || true)
  printf '%s,%s,%s,%s,%s,%s,%s\n' "$n" "$rc" "$wall" "$peak" "$mcpu" "$mmem" "$done_n" >> "$base/summary.csv"
done
printf 'finish=%s\n' "$(date -u +%FT%TZ)" >> "$base/metadata.txt"
