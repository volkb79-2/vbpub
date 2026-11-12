#!/usr/bin/env bash
# Description: supervise a native Assay R2 campaign across budget exits.
# usage: run-r2-campaign.sh <campaign>-s<N> <checkout-commit> <project-dir> <lane> [options] [run-gate args...]
#        run-r2-campaign.sh stop <campaign>
#        run-r2-campaign.sh status <campaign>
# options (consumed here, not passed to Run-Gate):
#   --instances K          run K shard instances of the lane at once (one lane
#                          container each, Run-Gate --shard), then one merge run
#   --shard-total T        shards in the whole campaign (default K); with
#   --shard-offset O       this host runs shards O..O+K-1 of T (cross-host split)
#   --merge-only T         only the merge run over T finished shard stores
#   --carry-over-main TEXT reuse killed/survived records of the checkout's own
#                          Assay store across a judge change (assay B530)
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
  echo "usage: $0 <campaign>-s<N> <checkout-commit> <project-dir> <lane> [--instances K [--shard-total T --shard-offset O]|--merge-only T] [--carry-over-main TEXT] [run-gate args...]" >&2
  echo "       $0 stop <campaign>" >&2
  echo "       $0 status <campaign>" >&2
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

remove_named_containers() {
  local names=$1 container rc=0
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

remove_lane_containers() {
  local start=$1 campaign_dir=$2 labels='' all_names='' records=()
  local record container rc=0 found=0
  command -v docker >/dev/null || {
    echo "cannot identify the lane container; stop it by exact name: docker is unavailable (campaign $start)" >&2
    return 1
  }

  # Newer Run-Gate versions carry both labels. The exact recorded names cover
  # older binaries, including the established 25.0.2 cockpit default.
  if labels=$(docker ps -a \
    --filter "label=run-gate.job=$start" \
    --filter "label=run-gate.campaign=$start" \
    --format '{{.Names}}' 2>/dev/null); then
    if [[ -n $labels ]]; then
      remove_named_containers "$labels" || rc=1
      found=1
    fi
  fi

  shopt -s nullglob
  records=("$campaign_dir"/attempt-*.container-name)
  shopt -u nullglob
  if (( ${#records[@]} )); then
    all_names=$(docker ps -a --format '{{.Names}}' 2>/dev/null) || {
      echo "stop: could not list Docker containers while checking the recorded lane name for $start" >&2
      return 1
    }
    for record in "${records[@]}"; do
      container=''
      IFS= read -r container < "$record" || true
      [[ $container =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || {
        echo "stop: invalid recorded Docker container name: $record" >&2
        rc=1
        continue
      }
      if grep -Fxq -- "$container" <<< "$all_names"; then
        docker rm -f "$container" >/dev/null || {
          echo "stop: could not remove exact lane container $container" >&2
          rc=1
        }
      fi
      found=1
    done
  fi

  if (( ! found )); then
    echo "cannot identify the lane container; stop it by exact name: inspect 'docker ps -a --format {{.Names}}' and remove the campaign's Run-Gate container (campaign $start)" >&2
    return 1
  fi
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
  local requested=$1 lock_file active_file cmd acquired=0 remove_rc=0
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
  if [[ -f $RUNNER_HOME/campaigns/$active_start/instances ]]; then
    # A fleet start owns no lane container itself: stop each child campaign
    # (its own lock, record and exact containers) that is still active.
    local child
    while IFS= read -r child; do
      [[ $child =~ $start_re ]] || continue
      [[ -e $RUNNER_HOME/locks/${BASH_REMATCH[1]}.active ]] || continue
      ( stop_campaign "$child" ) || remove_rc=1
    done < "$RUNNER_HOME/campaigns/$active_start/instances"
  else
    remove_lane_containers "$active_start" "$RUNNER_HOME/campaigns/$active_start" || remove_rc=1
  fi

  if (( acquired )); then
    rm -f -- "$active_file"
    flock -u 9
    (( remove_rc == 0 )) || return 1
  else
    local i
    for ((i = 0; i < 50; i++)); do
      if flock -n 9; then
        rm -f -- "$active_file"
        flock -u 9
        (( remove_rc == 0 )) || return 1
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

count_candidate_events() {  # <file>...: completed `candidate` events
  python3 - "$@" <<'PY'
import json
import sys

count = 0
for name in sys.argv[1:]:
    try:
        with open(name, encoding="utf-8") as stream:
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

status_campaign() {
  local requested=$1 base dir latest child children=() line
  split_campaign_name "$requested" || return $?
  base=$campaign_base
  shopt -s nullglob
  local starts=("$RUNNER_HOME/campaigns/$base"-s*)
  shopt -u nullglob
  (( ${#starts[@]} )) || { echo "no campaign start for $base" >&2; return 1; }
  latest=$(printf '%s\n' "${starts[@]}" | sed 's/.*-s\([0-9]*\)$/\1 &/' | sort -n | tail -1 | cut -d' ' -f2)
  [[ -z $campaign_start ]] || latest=$RUNNER_HOME/campaigns/$campaign_start
  [[ -d $latest ]] || { echo "no campaign start $requested" >&2; return 1; }
  children=("$latest")
  if [[ -f $latest/instances ]]; then
    while IFS= read -r child; do
      children+=("$RUNNER_HOME/campaigns/$child")
    done < "$latest/instances"
  fi
  for dir in "${children[@]}"; do
    line="$(basename "$dir"):"
    if [[ -f $dir/final-exit ]]; then
      line+=" final_exit=$(cat "$dir/final-exit")"
    elif [[ -d $dir ]]; then
      line+=" running"
    else
      line+=" not-started"
    fi
    if [[ -f $dir/attempts.log ]]; then
      line+=" last=[$(tail -n 1 "$dir/attempts.log")]"
    fi
    if [[ -f $dir/progress-file ]]; then
      line+=" completed_candidates=$(count_candidate_events "$(cat "$dir/progress-file")")"
    fi
    printf '%s\n' "$line"
    if [[ -f $dir/ksm-status ]]; then
      printf '  %s\n' "$(<"$dir/ksm-status")"
    fi
  done
}

if [[ ${1:-} == stop ]]; then
  [[ $# == 2 ]] || usage
  stop_campaign "$2"
  exit $?
fi
if [[ ${1:-} == status ]]; then
  [[ $# == 2 ]] || usage
  status_campaign "$2"
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

lane_container_patterns=$(python3 - "$checkout" "$rel" "$lane" "$commit" <<'PY'
import re
import subprocess
import sys
from pathlib import Path

checkout, rel, lane, commit = sys.argv[1:]
checkout_basename = Path(checkout).name
new_repo = checkout_basename
if re.fullmatch(r"[0-9a-f]{12,64}", checkout_basename):
    try:
        remote = subprocess.run(
            ["git", "-C", checkout, "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, check=False,
        )
        tail = remote.stdout.strip().rstrip("/").rsplit("/", 1)[-1]
        tail = tail.rsplit(":", 1)[-1].removesuffix(".git")
        if remote.returncode == 0 and tail:
            new_repo = tail
    except OSError:
        pass

def sanitize(value, limit):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "")
    cleaned = cleaned.lstrip("-_.")[:limit].rstrip("-_.")
    return cleaned or "x"

repo_part = sanitize(new_repo, 24)
project_part = sanitize(Path(rel).name or new_repo, 24)
lane_part = sanitize(lane, 32)
patterns = (
    rf"{re.escape(repo_part)}-run-gate-{re.escape(project_part)}-"
    rf"{re.escape(lane_part)}-{re.escape(commit[:8])}-[0-9]{{6}}-"
    r"[0-9]{4}-[0-9]{9}",
    # Run-Gate 25.0.2 uses repo.name for a plain clone, so this is the full
    # persistent checkout basename, not the remote URL tail or a short name.
    rf"run-gate-{re.escape(checkout_basename)}-{re.escape(lane)}-"
    r"[1-9][0-9]*-[1-9][0-9]*",
)
print("\n".join(patterns))
PY
) || lane_container_patterns=''

clamp_jobs() {  # DST-37: never pass --jobs above the lane's judge.mutation.jobs_max
  local toml=$checkout/$rel/assay.toml i max
  [[ -f $toml ]] || return 0
  max=$(python3 - "$toml" "$(lane_assay_name)" <<'PY'
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

# Each option's PRESENCE is tracked apart from its value: a given option
# with a zero, empty or malformed value is refused by name, never read as
# "absent" (review: `--instances 0` must not start an ordinary campaign).
instances=0; shard_total=0; shard_offset=0; merge_only=0; carry_main=''
declare -A given=()
fleet_option() {  # <option> <value> <min> <max>: a decimal in [min, max]
  local option=$1 value=$2 min=$3 max=$4
  [[ $value =~ ^(0|[1-9][0-9]{0,3})$ ]] && (( value >= min && value <= max )) || {
    echo "$option must be a decimal integer $min..$max (got '$value')" >&2; exit 2; }
}
while (( $# )); do
  case "$1" in
    --instances|--shard-total|--shard-offset|--merge-only|--carry-over-main)
      [[ $# -ge 2 ]] || usage; option=$1; value=$2; shift 2 ;;
    --instances=*|--shard-total=*|--shard-offset=*|--merge-only=*|--carry-over-main=*)
      option=${1%%=*}; value=${1#*=}; shift ;;
    *) args+=("$1"); shift; continue ;;
  esac
  [[ -z ${given[$option]:-} ]] || { echo "$option is given twice" >&2; exit 2; }
  given[$option]=1
  case "$option" in
    --instances) fleet_option "$option" "$value" 1 16; instances=$value ;;
    --shard-total) fleet_option "$option" "$value" 1 64; shard_total=$value ;;
    --shard-offset) fleet_option "$option" "$value" 0 63; shard_offset=$value ;;
    --merge-only) fleet_option "$option" "$value" 1 64; merge_only=$value ;;
    --carry-over-main)
      [[ -n ${value//[[:space:]]/} && $value != *$'\n'* ]] || {
        echo '--carry-over-main needs a non-empty one-line reason (why the judge change cannot affect outcomes)' >&2; exit 2; }
      carry_main=$value ;;
  esac
done
if [[ -n ${given[--instances]:-} || -n ${given[--merge-only]:-} ]]; then
  for value in "${args[@]}"; do
    case "$value" in
      --shard|--shard=*|--merge-shards|--merge-shards=*)
        echo "use --instances/--merge-only instead of passing $value through" >&2; exit 2 ;;
    esac
  done
fi
if [[ -n ${given[--merge-only]:-} ]]; then
  [[ -z ${given[--instances]:-}${given[--shard-total]:-}${given[--shard-offset]:-} ]] || {
    echo '--merge-only cannot be combined with --instances/--shard-total/--shard-offset' >&2; exit 2; }
elif [[ -n ${given[--instances]:-} ]]; then
  [[ -n ${given[--shard-total]:-} ]] || shard_total=$instances
  (( shard_offset + instances <= shard_total )) || {
    echo "--shard-offset $shard_offset + --instances $instances exceeds --shard-total $shard_total" >&2; exit 2; }
elif [[ -n ${given[--shard-total]:-}${given[--shard-offset]:-} ]]; then
  echo '--shard-total/--shard-offset require --instances' >&2; exit 2
fi

flex_floor_check() {
  # Functional floor (review): fleet and carry-over modes need a Run-Gate with
  # RG-265/RG-266 (--shard, STRICT --merge-shards, --carry-over); that
  # Run-Gate itself refuses an Assay judge below 12.2.0 by name before
  # admission. An older host Run-Gate (e.g. 25.0.2 / 25.4.0) is refused here,
  # before any campaign state; the legacy single-campaign path (none of these
  # options) never probes and keeps working on it.
  local help
  help=$(bash "$here/run-lane.sh" "$commit" "$rel" --help 2>&1) || {
    echo "functional floor: cannot read the host Run-Gate's --help (run-lane.sh failed); refusing --instances/--merge-only/--carry-over-main" >&2
    exit 75; }
  if [[ $help != *"--shard K/N"* || $help != *"--merge-shards N"* || $help != *STRICT* ||
        $help != *"--carry-over DIR"* ]]; then
    echo "functional floor: the host Run-Gate lacks RG-265 (its --help must list --shard K/N, the STRICT --merge-shards N and --carry-over DIR); --instances/--merge-only/--carry-over-main need a Run-Gate with RG-265/RG-266 and Assay >= 12.2.0. Upgrade the cockpit Run-Gate (RUN_GATE_BIN) or run the legacy single campaign without these options" >&2
    exit 2
  fi
}
if [[ -n ${given[--instances]:-}${given[--merge-only]:-}${given[--carry-over-main]:-} ]]; then
  flex_floor_check
fi
if [[ -n ${given[--carry-over-main]:-} ]]; then
  # The checkout's own Assay store as Run-Gate sees it inside the cockpit
  # (run-lane.sh mounts the checkout at /workspaces/<commit>).
  args+=(--carry-over "/workspaces/$commit/.run-gate/assay-state/$rel"
         --carry-over-reason "$carry_main")
fi

lane_assay_name() {  # run-gate lane -> Assay lane from the committed lane declaration
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

lane_memory_mib() {  # declared lane memory ceiling, rounded up to MiB, or empty
  python3 - "$checkout" "$rel" "$lane" <<'PY' 2>/dev/null || true
import re, sys, tomllib
root, rel, lane = sys.argv[1:4]
try:
    decl = tomllib.load(open(f"{root}/{rel}/run-gate.toml", "rb")).get("lanes", {}).get(lane) or {}
except (OSError, tomllib.TOMLDecodeError):
    sys.exit(0)
res = decl.get("resources") if isinstance(decl.get("resources"), dict) else {}
value = res.get("memory", decl.get("memory"))
match = re.fullmatch(r"([0-9]+)([bkmg]?)", str(value or "").lower())
if match:
    n = int(match.group(1))
    unit = match.group(2)
    divisor = {"": 1048576, "b": 1048576, "k": 1024}.get(unit)
    if divisor:
        print(max(1, (n + divisor - 1) // divisor))
    else:
        print(max(1, n if unit == "m" else n * 1024))
PY
}

lane_jobs_max() {  # Assay's hard worker maximum, defaulting to its serial behavior
  python3 - "$checkout/$rel/assay.toml" "$(lane_assay_name)" <<'PY' 2>/dev/null || echo 1
import sys, tomllib
try:
    cfg = tomllib.load(open(sys.argv[1], "rb"))
except (OSError, tomllib.TOMLDecodeError):
    print(1)
    raise SystemExit
mut = cfg.get("lanes", {}).get(sys.argv[2], {}).get("judge", {}).get("mutation", {})
value = mut.get("jobs_max", mut.get("jobs", 1))
print(value if type(value) is int and 0 < value < 10000000 else 1)
PY
}

lane_jobs_setting() {  # Run-Gate lane-level fixed count or auto selection
  python3 - "$checkout/$rel/run-gate.toml" "$lane" <<'PY' 2>/dev/null || true
import sys, tomllib
try:
    decl = tomllib.load(open(sys.argv[1], "rb")).get("lanes", {}).get(sys.argv[2]) or {}
except (OSError, tomllib.TOMLDecodeError):
    raise SystemExit
value = decl.get("jobs")
if type(value) is int and value > 0:
    print(value)
elif value == "auto":
    print("auto")
PY
}

lane_is_native_assay() {
  python3 - "$checkout/$rel/run-gate.toml" "$lane" <<'PY' 2>/dev/null
import sys, tomllib
try:
    decl = tomllib.load(open(sys.argv[1], "rb")).get("lanes", {}).get(sys.argv[2]) or {}
except (OSError, tomllib.TOMLDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if decl.get("kind") == "assay" else 1)
PY
}

workers_per_instance() {  # exact --jobs, configured count, or conservative auto ceiling
  local selected='' configured max
  local i
  for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ ${args[i]} == --jobs ]]; then
      selected=${args[i+1]:-}
      break
    elif [[ ${args[i]} =~ ^--jobs=(.+)$ ]]; then
      selected=${BASH_REMATCH[1]}
      break
    fi
  done
  max=$(lane_jobs_max)
  if [[ $selected =~ ^[1-9][0-9]*$ ]]; then
    (( selected > max )) && selected=$max
    printf '%s\n' "$selected"
    return
  fi
  if [[ $selected == auto ]]; then
    printf '%s\n' "$max"
    return
  fi
  configured=$(lane_jobs_setting)
  if [[ $configured =~ ^[1-9][0-9]*$ ]]; then
    (( configured > max )) && configured=$max
    printf '%s\n' "$configured"
  elif [[ $configured == auto ]]; then
    printf '%s\n' "$max"
  else
    # Run-Gate leaves Assay's serial default alone when jobs is omitted.
    printf '1\n'
  fi
}

read_meminfo_mib() {  # <field>: return a field from R2_MEMINFO in whole MiB, or empty
  local field=$1
  awk -v key="$field:" '$1 == key {print int($2 / 1024); found=1; exit} END {if (!found) exit 1}' \
    "${R2_MEMINFO:-/proc/meminfo}" 2>/dev/null || true
}

read_ksm_value() {  # <file>: decimal sysfs value or unavailable
  local value
  if [[ -r $1 ]] && IFS= read -r value < "$1" && [[ $value =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
  else
    printf 'unavailable\n'
  fi
}

ksm_preflight() {
  local root=${R2_KSM_SYSFS:-/sys/kernel/mm/ksm}
  KSM_RUN=$(read_ksm_value "$root/run")
  local sharing shared eligibility
  sharing=$(read_ksm_value "$root/pages_sharing")
  shared=$(read_ksm_value "$root/pages_shared")
  # The runner cannot infer a process's MADV_MERGEABLE / PR_SET_MEMORY_MERGE
  # opt-in from its lane declaration. Report this as unverified, not enabled.
  eligibility=unverified
  KSM_INFO="KSM: run=$KSM_RUN pages_sharing=$sharing pages_shared=$shared lane_eligibility=$eligibility (workloads must opt in with MADV_MERGEABLE or PR_SET_MEMORY_MERGE)"
  echo "INFO: $KSM_INFO" >&2
}

cpu_saturation_hint() {
  (( ! instances && ! merge_only )) || return 0
  local arg
  for arg in "${args[@]}"; do
    [[ $arg != --shard && $arg != --shard=* && $arg != --merge-shards && $arg != --merge-shards=* ]] || return 0
  done
  lane_is_native_assay || return 0
  local workers cpus advised
  workers=$(workers_per_instance)
  [[ $workers =~ ^[1-9][0-9]*$ ]] || return 0
  cpus=$(nproc 2>/dev/null || true)
  [[ $cpus =~ ^[1-9][0-9]*$ ]] || return 0
  (( workers < cpus )) || return 0
  advised=$((cpus / workers))
  (( advised > 1 )) || return 0
  echo "CPU hint: $workers worker(s) per native R2 instance on $cpus CPUs; if each worker is single-threaded, --instances $advised would use the available CPU slots. This is advice only; it does not change the run." >&2
}

fleet_memory_guard() {
  # Run-Gate's resources.memory remains the hard ceiling on every container.
  # This admission estimate scales that ceiling by workers, then admits only
  # expected peaks against RAM plus a bounded, host-visible swap allowance.
  local count=$1 lane_cap instance_override worker_peak jobs_max workers
  local reserve avail swap_total swap_free overcommit factor100 whole frac
  local scaled_cap expected_instance expected_total base extra swap_allowance budget
  instance_override=${R2_INSTANCE_MEM_MIB:-}
  lane_cap=$(lane_memory_mib)
  [[ -n $lane_cap || -n $instance_override ]] || {
    echo "memory guard: lane $lane declares no resources.memory, so Run-Gate applies no container memory cap and $count instance(s) are unbounded; set R2_INSTANCE_MEM_MIB to the measured per-container peak or declare resources.memory" >&2
    exit 2; }
  if [[ -n $lane_cap ]]; then
    [[ $lane_cap =~ ^[1-9][0-9]{0,6}$ ]] || {
      echo "memory guard: lane $lane resources.memory must be below 10^7 MiB (got '$lane_cap')" >&2; exit 2; }
  fi
  reserve=${R2_HOST_RESERVE_MIB:-1024}
  [[ $reserve =~ ^(0|[1-9][0-9]{0,6})$ ]] || {
    echo "memory guard: R2_HOST_RESERVE_MIB must be decimal MiB below 10^7 (got '$reserve')" >&2; exit 2; }
  if [[ -n $instance_override ]]; then
    [[ $instance_override =~ ^[1-9][0-9]{0,6}$ ]] || {
      echo "memory guard: R2_INSTANCE_MEM_MIB must be decimal MiB below 10^7 (got '$instance_override')" >&2; exit 2; }
  fi
  if [[ -n ${R2_WORKER_MEM_MIB:-} ]]; then
    [[ $R2_WORKER_MEM_MIB =~ ^[1-9][0-9]{0,6}$ ]] || {
      echo "memory guard: R2_WORKER_MEM_MIB must be decimal MiB below 10^7 (got '$R2_WORKER_MEM_MIB')" >&2; exit 2; }
  fi
  avail=$(read_meminfo_mib MemAvailable)
  [[ $avail =~ ^[0-9]{1,12}$ ]] || { echo 'memory guard: cannot read MemAvailable' >&2; exit 75; }
  swap_total=$(read_meminfo_mib SwapTotal)
  swap_free=$(read_meminfo_mib SwapFree)
  [[ $swap_total =~ ^[0-9]{1,12}$ && $swap_free =~ ^[0-9]{1,12}$ ]] || {
    echo 'memory guard: cannot read SwapTotal / SwapFree' >&2; exit 75; }
  workers=$(workers_per_instance)
  jobs_max=$(lane_jobs_max)
  [[ $workers =~ ^[1-9][0-9]*$ && $jobs_max =~ ^[1-9][0-9]*$ ]] || {
    echo 'memory guard: cannot determine the lane worker count' >&2; exit 75; }
  (( workers <= jobs_max )) || workers=$jobs_max

  if [[ -n $lane_cap ]]; then
    scaled_cap=$(((lane_cap * workers + jobs_max - 1) / jobs_max))
    (( scaled_cap >= 128 )) || scaled_cap=128
  else
    scaled_cap=not-applicable
  fi
  if [[ -n $instance_override ]]; then
    expected_instance=$instance_override
    worker_peak=not-applicable
  else
    if [[ -n ${R2_WORKER_MEM_MIB:-} ]]; then
      worker_peak=$R2_WORKER_MEM_MIB
    else
      worker_peak=$(((lane_cap + jobs_max - 1) / jobs_max))
    fi
    expected_instance=$((worker_peak * workers))
    (( expected_instance >= 128 )) || expected_instance=128
  fi
  expected_total=$((count * expected_instance))

  if [[ -n ${R2_OVERCOMMIT:-} ]]; then
    overcommit=$R2_OVERCOMMIT
  elif (( swap_total > 0 )) && [[ $KSM_RUN == 1 ]]; then
    overcommit=2.0
  else
    overcommit=1.0
  fi
  [[ $overcommit =~ ^([1-4])([.][0-9]{1,2})?$ ]] || {
    echo "memory guard: R2_OVERCOMMIT must be a decimal factor from 1.0 to 4.0 (got '$overcommit')" >&2; exit 2; }
  whole=${BASH_REMATCH[1]}; frac=${BASH_REMATCH[2]#\.}; frac=${frac:-0}
  ((${#frac} == 1)) && frac="${frac}0"
  factor100=$((whole * 100 + 10#$frac))
  (( factor100 >= 100 && factor100 <= 400 )) || {
    echo "memory guard: R2_OVERCOMMIT must be a decimal factor from 1.0 to 4.0 (got '$overcommit')" >&2; exit 2; }
  base=$((avail - reserve)); (( base > 0 )) || base=0
  extra=$((base * (factor100 - 100) / 100))
  swap_allowance=$swap_free
  (( swap_allowance <= extra )) || swap_allowance=$extra
  budget=$((base + swap_allowance))
  if (( expected_total > budget )); then
    echo "memory guard: expected $count instance(s) x $expected_instance MiB = $expected_total MiB; lane_cap=${lane_cap:-uncapped} MiB, workers_per_instance=$workers/$jobs_max, scaled_lane_ceiling=$scaled_cap MiB, worker_peak=$worker_peak MiB; MemAvailable=$avail MiB - reserve=$reserve MiB = $base MiB; overcommit=$overcommit, SwapFree=$swap_free MiB, swap_allowance=$swap_allowance MiB, budget=$budget MiB; expected peak exceeds budget (R2_INSTANCE_MEM_MIB overrides the per-instance estimate; R2_WORKER_MEM_MIB overrides the per-worker estimate)" >&2
    exit 75
  fi
  echo "memory guard: expected $count instance(s) x $expected_instance MiB = $expected_total MiB; lane_cap=${lane_cap:-uncapped} MiB, workers_per_instance=$workers/$jobs_max, scaled_lane_ceiling=$scaled_cap MiB, worker_peak=$worker_peak MiB; MemAvailable=$avail MiB - reserve=$reserve MiB = $base MiB; overcommit=$overcommit, SwapFree=$swap_free MiB, swap_allowance=$swap_allowance MiB, budget=$budget MiB; expected peak fits" >&2
}

if (( ! merge_only )); then clamp_jobs; fi
ksm_preflight
if (( instances )); then fleet_memory_guard "$instances"; fi
cpu_saturation_hint
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
printf '%s\n' "$KSM_INFO" > "$dir/ksm-status"
printf 'started_utc=%s\nstart=%s\ncommit=%s\nproject=%s\nlane=%s\nargs=%s\n' \
  "$(date -u +%FT%TZ)" "$start" "$commit" "$rel" "$lane" "${args[*]}" > "$dir/metadata.txt"

run_fleet() {
  # Several shard instances of one lane at once (each its own child campaign
  # with its own lock, lane container, attempts and stall counter), then one
  # merge run that folds the shard stores into the lane's main store and
  # produces the whole-lane verdict.
  local assay_lane suffix k child child_start rc merge_start='' merge_rc=''
  local -a child_starts=() child_pids=() child_exits=()
  local stagger=${R2_INSTANCE_STAGGER_S:-20} script fleet_final fleet_reason
  [[ $stagger =~ ^[0-9]+$ ]] || { echo 'R2_INSTANCE_STAGGER_S must be decimal seconds' >&2; return 2; }
  assay_lane=$(lane_assay_name)
  suffix=${start##*-s}
  script="$here/$(basename -- "${BASH_SOURCE[0]}")"
  : > "$dir/instances"
  fleet_children=()

  fleet_write_active() {
    local tmp="$control_file.$$" identity
    identity=$(proc_identity "$$")
    printf 'start=%s\nsupervisor=%s\nsupervisor_start=%s\nlane=\nlane_start=\nsampler=\nsampler_start=\n' \
      "$start" "$$" "${identity#* }" > "$tmp"
    mv -f -- "$tmp" "$control_file"
  }
  fleet_signal() {
    local signal=$1 code=143 pid
    [[ $signal != INT ]] || code=130
    trap - TERM INT
    printf '%s signal=%s\n' "$(date -u +%FT%TZ)" "$signal" >> "$dir/attempts.log"
    for pid in "${fleet_children[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
    for pid in "${fleet_children[@]}"; do wait "$pid" 2>/dev/null || true; done
    fleet_write_final "$code" cancelled "$signal"
    exit "$code"
  }
  fleet_cleanup() {
    local rc=$?
    trap - EXIT TERM INT
    if [[ -f $control_file && ! -L $control_file ]]; then
      local current
      current=$(sed -n 's/^start=//p' "$control_file")
      [[ $current != "$start" ]] || rm -f -- "$control_file"
    fi
    exit "$rc"
  }
  fleet_write_final() {
    local exit_code=$1 reason=$2 stop_reason=$3
    printf '%s\n' "$exit_code" > "$dir/final-exit.tmp"
    mv -f -- "$dir/final-exit.tmp" "$dir/final-exit"
    python3 - "$dir" "$start" "$campaign_base" "$commit" "$rel" "$lane" \
      "$exit_code" "$reason" "$stop_reason" "$merge_start" "$merge_rc" \
      "${child_starts[@]+"${child_starts[@]}"}" > "$dir/campaign-summary.json.tmp" <<'PY'
import json, os, sys
(directory, campaign, base, commit, project, lane, final_exit, reason,
 stop_reason, merge_start, merge_rc, *children) = sys.argv[1:]
campaigns = os.path.dirname(directory)

def child(name):
    entry = {"campaign": name, "final_exit": None, "attempts": None}
    try:
        summary = json.load(open(os.path.join(campaigns, name, "campaign-summary.json")))
        entry.update(final_exit=summary.get("final_exit"), attempts=summary.get("attempts"),
                     stop_reason=summary.get("stop_reason"))
    except (OSError, ValueError):
        pass
    return entry

print(json.dumps({
    "campaign": campaign, "campaign_base": base, "commit": commit,
    "project": project, "lane": lane, "mode": "fleet",
    "final_exit": int(final_exit),
    "result": "PASS" if final_exit == "0" else "NOT_PASS",
    "reason": reason, "stop_reason": stop_reason or None,
    "instances": [child(name) for name in children],
    "merge": child(merge_start) if merge_start else None,
}, separators=(",", ":")))
PY
    mv -f -- "$dir/campaign-summary.json.tmp" "$dir/campaign-summary.json"
    printf 'finished_utc=%s\nfinal_exit=%s\nreason=%s\n' \
      "$(date -u +%FT%TZ)" "$exit_code" "$reason" >> "$dir/metadata.txt"
  }

  trap fleet_cleanup EXIT
  trap 'fleet_signal TERM' TERM
  trap 'fleet_signal INT' INT
  fleet_write_active
  printf 'mode=fleet\ninstances=%s\nshard_total=%s\nshard_offset=%s\nmerge_only=%s\n' \
    "$instances" "$shard_total" "$shard_offset" "$merge_only" >> "$dir/metadata.txt"

  if (( instances )); then
    for ((k = shard_offset; k < shard_offset + instances; k++)); do
      child_start="$campaign_base.i${k}of${shard_total}-s$suffix"
      child_starts+=("$child_start")
      printf '%s\n' "$child_start" >> "$dir/instances"
      R2_INSTANCE_ARTIFACT="$assay_lane.$lane.shard${k}of${shard_total}" \
        bash "$script" "$child_start" "$commit" "$rel" "$lane" \
          "${args[@]+"${args[@]}"}" --shard "$k/$shard_total" \
          > "$dir/$child_start.log" 2>&1 &
      fleet_children+=("$!")
      child_pids+=("$!")
      printf '%s instance=%s pid=%s\n' "$(date -u +%FT%TZ)" "$child_start" "$!" >> "$dir/attempts.log"
      if (( k + 1 < shard_offset + instances && stagger > 0 )); then sleep "$stagger"; fi
    done
    for ((k = 0; k < ${#child_pids[@]}; k++)); do
      set +e; wait "${child_pids[k]}"; rc=$?; set -e
      child_exits+=("$rc")
      printf '%s instance=%s exit=%s\n' "$(date -u +%FT%TZ)" "${child_starts[k]}" "$rc" >> "$dir/attempts.log"
    done
    fleet_children=()
  fi

  if (( merge_only )) || (( shard_offset == 0 && instances == shard_total )); then
    local total=$merge_only
    (( total )) || total=$shard_total
    merge_start="$campaign_base.merge-s$suffix"
    printf '%s\n' "$merge_start" >> "$dir/instances"
    R2_INSTANCE_ARTIFACT="$assay_lane" \
      bash "$script" "$merge_start" "$commit" "$rel" "$lane" \
        "${args[@]+"${args[@]}"}" --merge-shards "$total" \
        > "$dir/$merge_start.log" 2>&1 &
    fleet_children+=("$!")
    set +e; wait "$!"; merge_rc=$?; set -e
    fleet_children=()
    printf '%s merge=%s exit=%s\n' "$(date -u +%FT%TZ)" "$merge_start" "$merge_rc" >> "$dir/attempts.log"
    if [[ -f $RUNNER_HOME/campaigns/$merge_start/verdict.json ]]; then
      cp -- "$RUNNER_HOME/campaigns/$merge_start/verdict.json" "$dir/verdict.json"
    fi
    fleet_final=$merge_rc
    fleet_reason='lane-exit'
    [[ $fleet_final != 0 ]] || fleet_reason=pass
  else
    # This host ran only part of the shards: the whole-lane verdict needs the
    # other hosts' shard stores and a `--merge-only` run (README).
    fleet_final=75
    fleet_reason=merge-pending
  fi
  fleet_write_final "$fleet_final" "$fleet_reason" ''
  return "$fleet_final"
}

if (( instances || merge_only )); then
  set +e
  run_fleet
  fleet_rc=$?
  set -e
  exit "$fleet_rc"
fi

select_new_lane_name() {
  local names=$1 baseline=$2 pattern_only=$3
  LANE_CONTAINER_NAMES=$names LANE_BASELINE_NAMES=$baseline \
    LANE_CONTAINER_PATTERNS=$lane_container_patterns \
    LANE_PATTERN_ONLY=$pattern_only python3 - <<'PY'
import os
import re

names = os.environ.get("LANE_CONTAINER_NAMES", "").splitlines()
baseline = set(os.environ.get("LANE_BASELINE_NAMES", "").splitlines())
patterns = [re.compile(item) for item in
            os.environ.get("LANE_CONTAINER_PATTERNS", "").splitlines() if item]
pattern_only = os.environ.get("LANE_PATTERN_ONLY") == "1"
safe = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
matches = []
for name in names:
    if not name or name in baseline or not safe.fullmatch(name):
        continue
    if pattern_only and not any(pattern.fullmatch(name) for pattern in patterns):
        continue
    matches.append(name)
matches = list(dict.fromkeys(matches))
if len(matches) == 1:
    print(matches[0])
PY
}

capture_lane_container_name() {
  local attempt_name=$1 baseline=$2 baseline_ok=$3 labels='' names='' container=''
  local record="$dir/$attempt_name.container-name"
  command -v docker >/dev/null || return 0
  while :; do
    if labels=$(docker ps -a \
      --filter "label=run-gate.job=$start" \
      --filter "label=run-gate.campaign=$start" \
      --format '{{.Names}}' 2>/dev/null); then
      container=$(select_new_lane_name "$labels" "$baseline" 0)
      if [[ -n $container ]]; then
        printf '%s\n' "$container" > "$record.tmp"
        mv -f -- "$record.tmp" "$record"
        return 0
      fi
    fi
    if (( baseline_ok )); then
      names=$(docker ps -a --format '{{.Names}}' 2>/dev/null) || names=''
      container=$(select_new_lane_name "$names" "$baseline" 1)
      if [[ -n $container ]]; then
        printf '%s\n' "$container" > "$record.tmp"
        mv -f -- "$record.tmp" "$record"
        return 0
      fi
    fi
    pid_running "$lane_pid" || return 0
    sleep 0.1
  done
}

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

# A shard instance (or the fleet's merge run) counts only its OWN progress
# file: the other instances' candidates must not mask a stalled instance.
if [[ -n ${R2_INSTANCE_ARTIFACT:-} ]]; then
  [[ $R2_INSTANCE_ARTIFACT =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || {
    echo 'invalid R2_INSTANCE_ARTIFACT' >&2; exit 2; }
  progress_glob="progress-$R2_INSTANCE_ARTIFACT.jsonl"
  printf '%s\n' "$progress_dir/$progress_glob" > "$dir/progress-file"
else
  progress_glob="progress-*.jsonl"
fi

candidate_count() {
  python3 - "$progress_dir" "$progress_glob" <<'PY'
import json
from pathlib import Path
import sys

count = 0
for path in Path(sys.argv[1]).glob(sys.argv[2]):
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
    remove_lane_containers "$start" "$dir" || true
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
  container_baseline=''
  container_baseline_ok=0
  if command -v docker >/dev/null \
    && container_baseline=$(docker ps -a --format '{{.Names}}' 2>/dev/null); then
    container_baseline_ok=1
  fi
  RUN_GATE_JOB=$start RUN_GATE_CAMPAIGN=$start \
    bash "$here/run-lane.sh" "$commit" "$rel" "$lane" "${args[@]}" \
      > "$dir/$attempt_name.log" 2>&1 &
  lane_pid=$!
  printf '%s\n' "$lane_pid" > "$dir/lane.pid"
  write_active
  capture_lane_container_name "$attempt_name" "$container_baseline" "$container_baseline_ok"
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

verdict_src=$progress_dir/verdict-${R2_INSTANCE_ARTIFACT:-$(resolve_assay_lane)}.json
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
