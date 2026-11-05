#!/usr/bin/env bash
# Description: supervise a native Assay R2 campaign: repeat `run-gate <lane>` while it exits 4 (budget) and still makes progress.
# usage: run-r2-campaign.sh <campaign-name> <checkout-commit> <project-dir> <lane> [run-gate args...]
# Run it detached: run-job <campaign-name>-sup -- bash run-r2-campaign.sh ...
# Per attempt: $RUNNER_HOME/campaigns/<name>/attempt-NN.log + attempt-NN.exit; memory/PSI samples every 10 s in samples.csv;
# final exit code in final-exit. Run-Gate resumes the same campaign state on a repeat call (`--resume --progress` is automatic).
set -Eeuo pipefail
umask 077
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
[[ $# -ge 4 ]] || { echo "usage: $0 <name> <checkout-commit> <project-dir> <lane> [run-gate args...]" >&2; exit 2; }
name=$1; commit=$2; rel=$3; lane=$4; shift 4
[[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo 'invalid campaign name' >&2; exit 2; }
clamp_jobs() {  # DST-37: never pass --jobs above the lane's judge.mutation.jobs_max
  local toml=$RUNNER_HOME/checkouts/$commit/$rel/assay.toml i max
  [[ -f $toml ]] || return 0
  max=$(python3 - "$toml" "$lane" <<'PY'
import sys, tomllib
cfg = tomllib.load(open(sys.argv[1], "rb"))
mut = cfg.get("lanes", {}).get(sys.argv[2], {}).get("judge", {}).get("mutation", {})
print(mut.get("jobs_max", mut.get("jobs", "")))  # assay accepts `jobs` as an alias of `jobs_max` (config.py)
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
args=("$@"); clamp_jobs; set -- "${args[@]}"
dir=$RUNNER_HOME/campaigns/$name
install -d -m 0700 "$RUNNER_HOME/campaigns" "$RUNNER_HOME/locks" "$dir"
exec 9>"$RUNNER_HOME/locks/$name.lock"
flock -n 9 || { echo "campaign $name is already supervised" >&2; exit 73; }
progress_dir=$RUNNER_HOME/checkouts/$commit/$rel/.assay
: > "$dir/.supervision-start"
printf 'started_utc=%s\ncommit=%s\nproject=%s\nlane=%s\nargs=%s\n' "$(date -u +%FT%TZ)" "$commit" "$rel" "$lane" "$*" >> "$dir/metadata.txt"

(
  while :; do
    printf '%s,%s,%s,%s\n' "$(date -u +%FT%TZ)" \
      "$(cat /sys/fs/cgroup/dev.slice/"$GATES_SLICE"/memory.current 2>/dev/null || echo 0)" \
      "$(awk '/^some/ {sub("avg10=","",$2); print $2}' /proc/pressure/cpu)" \
      "$(awk '/^some/ {sub("avg10=","",$2); print $2}' /proc/pressure/memory)" >> "$dir/samples.csv"
    sleep 10
  done
) &
sampler=$!
trap 'kill "$sampler" 2>/dev/null || true' EXIT

events() { { cat "$progress_dir"/progress-*.jsonl 2>/dev/null || true; } | wc -l; }
attempt=1; stalled=0; final=0
while :; do
  identity_check
  before=$(events)
  set +e
  bash "$here/run-lane.sh" "$commit" "$rel" "$lane" "$@" > "$dir/$(printf 'attempt-%02d' "$attempt").log" 2>&1
  rc=$?
  set -e
  printf '%s\n' "$rc" > "$dir/$(printf 'attempt-%02d' "$attempt").exit"
  after=$(events)
  printf '%s attempt=%s exit=%s progress_events=%s->%s\n' "$(date -u +%FT%TZ)" "$attempt" "$rc" "$before" "$after" >> "$dir/attempts.log"
  if [[ $rc != 4 ]]; then final=$rc; break; fi
  if (( after <= before )); then stalled=$((stalled + 1)); else stalled=0; fi
  if (( stalled >= 2 || attempt >= ${R2_MAX_ATTEMPTS:-12} )); then final=$rc; echo "stopping: stalled=$stalled attempt=$attempt" >> "$dir/attempts.log"; break; fi
  attempt=$((attempt + 1))
done
printf '%s\n' "$final" > "$dir/final-exit"
printf 'finished_utc=%s\nfinal_exit=%s\n' "$(date -u +%FT%TZ)" "$final" >> "$dir/metadata.txt"
# <campaign>/campaign-summary.json: a summary a board can read without parsing logs. NOT an Assay verdict (no outcome, no hash chain).
printf '{"campaign": "%s", "commit": "%s", "project": "%s", "lane": "%s", "attempts": %s, "final_exit": %s, "result": "%s", "finished_utc": "%s"}\n' \
  "$name" "$commit" "$rel" "$lane" "$attempt" "$final" "$([[ $final == 0 ]] && echo PASS || echo NOT_PASS)" "$(date -u +%FT%TZ)" > "$dir/campaign-summary.json"
# <campaign>/verdict.json: the lane's OWN Assay verdict, byte for byte (run-gate writes it as <project>/.assay/verdict-<assay_lane>.json);
# dstdns r2_campaign.py runs `assay verify` on it and needs outcome PASS. Only a file written by this supervision counts (newer than the start marker).
resolve_assay_lane() {  # run-gate lane -> assay lane: project run-gate.toml, then run-gate.root.toml, else the lane name (also for [assay.import] lanes)
  python3 - "$RUNNER_HOME/checkouts/$commit" "$rel" "$lane" <<'PY' 2>/dev/null || echo "$lane"
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
  echo "campaign $name finished with exit 0 but the lane wrote no fresh Assay verdict at $verdict_src; refusing to report success" >&2
  printf 'verdict_missing=%s\n' "$verdict_src" >> "$dir/metadata.txt"
  exit 70
else
  printf 'verdict_missing=%s\n' "$verdict_src" >> "$dir/metadata.txt"
fi
exit "$final"
