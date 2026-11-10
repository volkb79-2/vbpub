#!/usr/bin/env bash
# Read-only controller snapshot. No fetch, SSH, Docker, or agent process control.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ $# == 0 ]] || { echo "usage: $0" >&2; exit 2; }

now=$(date +%s)
age() {
  local then=${1:-} delta
  [[ $then =~ ^[0-9]+$ ]] || { echo '?'; return; }
  delta=$((now - then)); (( delta < 0 )) && delta=0
  if (( delta < 90 )); then echo "${delta}s"
  elif (( delta < 5400 )); then echo "$((delta / 60))m"
  else echo "$((delta / 3600))h$(((delta % 3600) / 60))m"; fi
}

echo "=== controller $(date -u '+%F %T')Z"
echo "home=$CONTROLLER_HOME repo=$CONTROLLER_REPO"
echo "--- repository"
if git -C "$CONTROLLER_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'head=%s branch=%s\n' "$(git -C "$CONTROLLER_REPO" rev-parse --short HEAD)" \
    "$(git -C "$CONTROLLER_REPO" branch --show-current)"
  git -C "$CONTROLLER_REPO" status --short | sed 's/^/  /'
  printf 'worktrees=%s\n' "$(git -C "$CONTROLLER_REPO" worktree list | wc -l)"
else
  echo "repository unavailable"
fi

echo "--- recent release logs"
shopt -s nullglob
release_logs=("$CONTROLLER_HOME"/logs/release-*.log)
shopt -u nullglob
if (( ${#release_logs[@]} == 0 )); then echo "  none"
else
  mapfile -t ordered < <(python3 - "${release_logs[@]}" <<'PY'
import os, sys
for path in sorted(sys.argv[1:], key=os.path.getmtime, reverse=True)[:2]:
    print(path)
PY
  )
  for file in "${ordered[@]}"; do
    [[ -n $file ]] || continue
    last=$(tail -n 1 "$file" 2>/dev/null | cut -c1-110)
    exit_line=$(grep -E '^EXIT=[0-9]+' "$file" 2>/dev/null | tail -n 1 || true)
    printf '  %s %s age=%s | %s\n' "$(basename "$file" .log)" "${exit_line:-RUNNING}" \
      "$(age "$(stat -c %Y "$file" 2>/dev/null || true)")" "$last"
  done
fi

echo "--- controller runs"
shopt -s nullglob
run_files=("$CONTROLLER_HOME"/runs/*.jsonl)
shopt -u nullglob
if (( ${#run_files[@]} == 0 )); then echo "  none"
else
  mapfile -t ordered < <(python3 - "${run_files[@]}" <<'PY'
import os, sys
for path in sorted(sys.argv[1:], key=os.path.getmtime, reverse=True)[:20]:
    print(path)
PY
  )
  for file in "${ordered[@]}"; do
    [[ -n $file ]] || continue
    name=${file##*/}; name=${name%.jsonl}; mtime=$(stat -c %Y "$file" 2>/dev/null || true)
    if [[ -f $CONTROLLER_HOME/runs/$name.done ]]; then
      printf '  %s %s age=%s\n' "$name" "$(head -c 40 "$CONTROLLER_HOME/runs/$name.done")" "$(age "$mtime")"
    else
      size=$(stat -c %s "$file" 2>/dev/null || echo '?')
      state="RUNNING (last write $(age "$mtime") ago, ${size} bytes)"
      [[ $mtime =~ ^[0-9]+$ ]] && (( now - mtime > 900 )) && state="$state STALLED?"
      printf '  %s %s\n' "$name" "$state"
    fi
  done
fi

echo "--- host queues"
if [[ -f $CONTROLLER_HOSTS_FILE ]]; then
  if hosts=$(controller_host_list); then
  if [[ -z $hosts ]]; then echo "  no hosts configured"
  else
    while IFS= read -r host; do
      [[ -n $host ]] || continue
      queue=$CONTROLLER_HOME/logs/host-sat-queue
      pid=$(cat "$queue/$host.pid" 2>/dev/null || true)
      if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then alive="alive pid=$pid"; else alive=DEAD; fi
      current=$(cut -c1-100 "$queue/$host.current" 2>/dev/null || true)
      read -r streak last_pass <<<"$(awk -F '\t' -v h="$host" '
        NR>1 && $2==h {
          if ($6 ~ /^INFRA/) streak++
          else if ($6 !~ /^PAUSED/) { streak=0; if ($6 ~ /^PASS/) pass=$1 }
        }
        END { printf "%d %s\n", streak, pass }
      ' "$CONTROLLER_HOME/logs/host-sat-results.tsv" 2>/dev/null)" || true
      streak=${streak:-0}; pass_epoch=""
      if [[ -n ${last_pass:-} ]]; then pass_epoch=$(date -u -d "$last_pass" +%s 2>/dev/null || true); fi
      [[ -n $pass_epoch ]] && pass_age="$(age "$pass_epoch") ago" || pass_age=never
      printf '  %s: loop %s | in flight: %s | INFRA streak=%s | last PASS=%s\n' \
        "$host" "$alive" "${current:-idle}" "$streak" "$pass_age"
      flag=$CONTROLLER_HOME/host-sat-queue.disabled.$host
      flag_newer=0
      if [[ -e $flag ]]; then
        flag_mtime=$(stat -c %Y "$flag" 2>/dev/null || true)
        printf '    host flag present age=%s\n' "$(age "$flag_mtime")"
        if [[ -s $flag ]]; then sed 's/^/      /' "$flag"; else echo '      pause metadata: absent (legacy empty flag)'; fi
        if [[ -z $pass_epoch || ${flag_mtime:-0} -gt $pass_epoch ]]; then flag_newer=1; fi
      else
        echo '    host flag: none'
      fi
      if [[ -e $CONTROLLER_HOME/host-sat-queue.disabled ]]; then
        echo '    global flag present'
        if [[ -s $CONTROLLER_HOME/host-sat-queue.disabled ]]; then
          sed 's/^/      /' "$CONTROLLER_HOME/host-sat-queue.disabled"
        else echo '      pause metadata: absent (legacy empty flag)'; fi
      fi
      if (( streak >= 2 || flag_newer == 1 )); then
        reasons=''
        (( streak >= 2 )) && reasons="INFRA streak=$streak"
        (( flag_newer == 1 )) && reasons="${reasons:+$reasons; }pause flag is newer than the last PASS"
        echo "    ATTENTION $host: $reasons"
      fi
    done <<< "$hosts"
  fi
  else
    echo "  invalid host table: $CONTROLLER_HOSTS_FILE"
  fi
else
  echo "  configure $CONTROLLER_HOSTS_FILE to inspect host queues"
fi
if [[ -f $CONTROLLER_HOME/logs/host-sat-results.tsv ]]; then
  echo '--- recent host queue results'
  tail -n 6 "$CONTROLLER_HOME/logs/host-sat-results.tsv" | \
    awk -F '\t' '{printf "  %s %s %s %s %ss\n",substr($1,12,5),$2,$3,$6,$7}'
fi
exit 0
