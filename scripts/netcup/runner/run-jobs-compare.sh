#!/usr/bin/env bash
# Description: run one registered assay lane at --jobs 1, 3, 6 and auto on fresh state and record wall time, peak gates memory, PSI and verdict hashes.
# usage: run-jobs-compare.sh <commit> <project-dir> <lane> <result-name> [jobs-list, default "1 3 6 auto"]
set -Eeuo pipefail
umask 077
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
commit=$1; rel=$2; lane=$3; name=$4; list=${5:-"1 3 6 auto"}
base=$RUNNER_HOME/campaigns/$name
install -d -m 0700 "$base"
co=$RUNNER_HOME/checkouts/$commit
state=$co/.run-gate/assay-state/$rel
assay=$co/$rel/.assay
printf 'commit=%s project=%s lane=%s list=%s start=%s\n' "$commit" "$rel" "$lane" "$list" "$(date -u +%FT%TZ)" >> "$base/metadata.txt"
printf 'jobs,exit,wall_s,peak_gates_mem_bytes,max_cpu_psi,max_mem_psi,verdict_sha256\n' > "$base/summary.csv"
for n in $list; do
  identity_check
  # fresh state for every run: move any previous state aside
  # (moved OUT of the checkout: a stray directory inside it makes the tree dirty and Assay refuses to judge)
  install -d -m 0700 "$base/old-$n"
  if [[ -e $state ]]; then mv "$state" "$base/old-$n/state.$(date +%s)"; fi
  if [[ -e $assay ]]; then mv "$assay" "$base/old-$n/assay.$(date +%s)"; fi
  mkdir -p "$state" "$assay"
  run=$base/jobs-$n; install -d -m 0700 "$run"
  : > "$run/samples.csv"
  (
    while :; do
      printf '%s,%s,%s,%s\n' "$(date -u +%FT%TZ)" \
        "$(cat /sys/fs/cgroup/dev.slice/"$GATES_SLICE"/memory.current 2>/dev/null || echo 0)" \
        "$(awk '/^some/ {sub("avg10=","",$2); print $2}' /proc/pressure/cpu)" \
        "$(awk '/^some/ {sub("avg10=","",$2); print $2}' /proc/pressure/memory)" >> "$run/samples.csv"
      sleep 2
    done
  ) &
  sampler=$!
  t0=$(date +%s.%N)
  set +e
  bash "$here/run-lane.sh" "$commit" "$rel" "$lane" --jobs "$n" > "$run/run.log" 2>&1
  rc=$?
  set -e
  t1=$(date +%s.%N)
  kill "$sampler" 2>/dev/null || true
  wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')
  peak=$(awk -F, 'NR>0 && $2>m {m=$2} END{print m+0}' "$run/samples.csv")
  mcpu=$(awk -F, '$3>m {m=$3} END{print m+0}' "$run/samples.csv")
  mmem=$(awk -F, '$4>m {m=$4} END{print m+0}' "$run/samples.csv")
  cp -a "$assay/." "$run/assay-out/" 2>/dev/null || { mkdir -p "$run/assay-out"; cp -a "$assay/." "$run/assay-out/" || true; }
  vsha=$(sha256sum "$assay"/verdict-*.json 2>/dev/null | awk '{print $1}' | head -1 || true)
  printf '%s,%s,%s,%s,%s,%s,%s\n' "$n" "$rc" "$wall" "$peak" "$mcpu" "$mmem" "$vsha" >> "$base/summary.csv"
done
printf 'finish=%s\n' "$(date -u +%FT%TZ)" >> "$base/metadata.txt"
