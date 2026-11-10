#!/usr/bin/env bash
# Run cmru release from a clean worktree after memory PSI is below its configured ceiling.
# Usage: release-from-wt.sh <worktree> <log-name-or-absolute-path> [--venv <path>] [--no-archive|--archive-evidence] -- <cmru release args...>
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ $# -ge 4 ]] || { echo "usage: $0 <worktree> <log-name-or-absolute-path> [--venv <path>] [--no-archive|--archive-evidence] -- <cmru release args...>" >&2; exit 2; }
worktree=$(realpath -e -- "$1") || exit 2
git -C "$worktree" rev-parse --show-toplevel >/dev/null 2>&1 || { echo "not a Git worktree: $worktree" >&2; exit 2; }
log=$2
shift 2
venv=${CMRU_VENV:-}
archive_mode=auto
while [[ $# -gt 0 && $1 != -- ]]; do
  case $1 in
    --venv) [[ $# -ge 2 ]] || { echo '--venv needs a path' >&2; exit 2; }; venv=$2; shift 2 ;;
    --no-archive) archive_mode=off; shift ;;
    --archive-evidence) archive_mode=on; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ ${1:-} == -- ]] || { echo "expected -- before cmru release arguments" >&2; exit 2; }
shift
[[ $# -gt 0 ]] || { echo 'cmru release arguments are required' >&2; exit 2; }
[[ -n $venv && -d $venv/bin ]] || { echo 'set CMRU_VENV to the chosen engine venv' >&2; exit 2; }
[[ $log == /* ]] || log=$CONTROLLER_HOME/logs/$log
mkdir -p -- "$(dirname -- "$log")"

if [[ -n $(git -C "$worktree" status --porcelain 2>/dev/null) ]]; then
  echo "refusing release from dirty worktree: $worktree" >&2
  exit 2
fi

branch=$(git -C "$worktree" branch --show-current 2>/dev/null || true)
if [[ $archive_mode == auto && $branch == rel-* ]] || [[ $archive_mode == on ]]; then
  short=$(git -C "$worktree" rev-parse --short HEAD) || exit 2
  archive_name=${branch//\//_}
  archive_root=$CONTROLLER_HOME/releases-archive
  archive=$archive_root/${archive_name:-detached}-$short-$(date -u +%Y%m%dT%H%M%SZ)-$$
  mapfile -t projects < <(git -C "$worktree" ls-tree --name-only -d HEAD)
  found=0
  for project in "${projects[@]}"; do
    for kind in logs artifacts evidence; do
      [[ -e $worktree/$project/$kind ]] && found=1
    done
  done
  if (( found )); then
    mkdir -p -- "$archive_root"
    mkdir -- "$archive"
    for project in "${projects[@]}"; do
      for kind in logs artifacts evidence; do
        source_dir=$worktree/$project/$kind
        if [[ -e $source_dir ]]; then
          mkdir -p -- "$archive/$project"
          cp -a -- "$source_dir" "$archive/$project/$kind"
        fi
      done
    done
    printf 'evidence archive: %s\n' "$archive"
  fi
fi

psi_file=${RELEASE_PSI_FILE:-/proc/pressure/memory}
psi_limit=${RELEASE_PSI_LIMIT:-25}
poll_seconds=${RELEASE_PSI_POLL_SECONDS:-20}
[[ $psi_limit =~ ^[0-9]+([.][0-9]+)?$ && $poll_seconds =~ ^[0-9]+$ ]] || {
  echo 'RELEASE_PSI_LIMIT must be numeric and RELEASE_PSI_POLL_SECONDS must be integer seconds' >&2; exit 2;
}
while :; do
  [[ -r $psi_file ]] || { echo "memory PSI file unreadable: $psi_file" >&2; exit 65; }
  pressure=$(awk '/^some / { for (i=1; i<=NF; i++) if ($i ~ /^avg10=/) { sub(/^avg10=/, "", $i); print $i; exit } }' "$psi_file")
  [[ $pressure =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "memory PSI avg10 missing in $psi_file" >&2; exit 65; }
  if awk -v value="$pressure" -v limit="$psi_limit" 'BEGIN { exit !(value < limit) }'; then break; fi
  sleep "$poll_seconds"
done

export PATH="$venv/bin:$PATH"
if [[ -n ${DOCKER_CONFIG_OVERRIDE:-} ]]; then export DOCKER_CONFIG=$DOCKER_CONFIG_OVERRIDE
elif [[ -n ${DOCKER_CONFIG:-} ]]; then export DOCKER_CONFIG
else unset DOCKER_CONFIG; fi
export BUILDX_BUILDER=${BUILDX_BUILDER:-default}
export BUILDKIT_HOST=${BUILDKIT_HOST:-}
[[ -z ${RUN_GATE_PROFILE:-} ]] || export RUN_GATE_PROFILE
printf 'start %s mem_psi_avg10=%s\n' "$(date -u +%T)" "$pressure" > "$log"
if cmru release "$@" >> "$log" 2>&1; then rc=0; else rc=$?; fi
printf 'EXIT=%s\n' "$rc" >> "$log"
exit "$rc"
