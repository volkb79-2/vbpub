#!/usr/bin/env bash
# Configured host queue for serial, light Run-Gate lanes. See README.md in this directory.
# Commands: start [host...], stop [host...], pause [host], resume [host], status, run <host>, probe <host>.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CONTROLLER_HOME=${CONTROLLER_HOME:-"${HOME:?}/controller"}
if [[ -f $SCRIPT_DIR/lib.sh ]]; then
  CONTROLLER_LIB=$SCRIPT_DIR/lib.sh
  REPO_ROOT=''
else
  REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
  CONTROLLER_LIB=$REPO_ROOT/scripts/controller/lib.sh
fi
# shellcheck source=../../controller/lib.sh
# shellcheck disable=SC1091
. "$CONTROLLER_LIB"
controller_init

BASE=$CONTROLLER_HOME
REPO=${CONTROLLER_REPO:-$REPO_ROOT}
if [[ ${BASH_SOURCE[0]} == "$0" ]] && ! git -C "$REPO" rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "controller: CONTROLLER_REPO must name a vbpub Git checkout (configured path: $REPO)" >&2
  exit 2
fi
Q=$BASE/logs/host-sat-queue
RESULTS=$BASE/logs/host-sat-results.tsv
HOSTS_FILE=$CONTROLLER_HOSTS_FILE
REMOTE_GATE_HOSTS=${REMOTE_GATE_HOSTS:-$HOSTS_FILE}
[[ $REMOTE_GATE_HOSTS == /* ]] || REMOTE_GATE_HOSTS=$CONTROLLER_HOME/$REMOTE_GATE_HOSTS
export REMOTE_GATE_HOSTS REMOTE_GATE_OUT=$BASE/remote-gates
PY=${CONTROLLER_PYTHON:-python3}
LOAD_FACTOR=${HOST_SAT_LOAD_FACTOR:-0.625}
PSI_LIMIT=${HOST_SAT_PSI_LIMIT:-25}
MEM_AVAIL_MIN_MB=${HOST_SAT_MEM_AVAIL_MIN_MB:-2500}
SWAP_GROW_MB=${HOST_SAT_SWAP_GROW_MB:-256}
SWAP_ABS_MB=${HOST_SAT_SWAP_ABS_MB:-4096}
ROOT_FREE_MIN_GB=${HOST_SAT_ROOT_FREE_MIN_GB:-5}
LANE_TIMEOUT_S=${HOST_SAT_LANE_TIMEOUT_S:-3600}
POLL_S=${HOST_SAT_POLL_S:-60}
RECHECK_S=${HOST_SAT_RECHECK_S:-120}
INFRA_BACKOFF_S=${HOST_SAT_INFRA_BACKOFF_S:-600}
MAX_CONSEC_INFRA=${HOST_SAT_MAX_CONSEC_INFRA:-5}
mkdir -p -- "$Q/runs"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(now) [$HOST] $*" >> "$Q/$HOST.log"; }

hostcmd() {
  local host=$1 command=$2 guard script quoted remote
  local -a ssh_args=()
  controller_host_argv ssh_args "$host" ssh || return 2
  guard=$(controller_host_scalar "$host" guard) || return 2
  script=$(printf 'set -e\n%s\n%s\n' "$guard" "$command")
  quoted=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$script") || return 2
  remote="bash -c $quoted"
  "${ssh_args[@]}" "$remote"
}

probe() {
  # shellcheck disable=SC2016
  hostcmd "$1" 'echo nproc=$(nproc); echo load1=$(cut -d" " -f1 /proc/loadavg);
    echo memsome=$(awk "/^some/{sub(\"avg10=\",\"\",\$2);print \$2}" /proc/pressure/memory);
    echo iofull=$(awk "/^full/{sub(\"avg10=\",\"\",\$2);print \$2}" /proc/pressure/io);
    echo memavail=$(awk "/^MemAvailable/{print int(\$2/1024)}" /proc/meminfo);
    echo swapused=$(awk "/^SwapTotal/{t=\$2}/^SwapFree/{f=\$2}END{print int((t-f)/1024)}" /proc/meminfo);
    echo oomkill=$(awk "/^oom_kill/{print \$2}" /proc/vmstat);
    echo rootfree=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)'
}

# returns 0 = safe to launch; otherwise prints the first stop reason
check_ok() {
  local out k v nproc='' load1='' memsome='' iofull='' memavail='' swapused='' oomkill='' rootfree=''
  out=$(probe "$HOST" 2>&1) || { echo "probe failed: $(echo "$out" | tail -1 | cut -c1-120)"; return 1; }
  while IFS='=' read -r k v; do
    case $k in
      nproc) nproc=$v;; load1) load1=$v;; memsome) memsome=$v;; iofull) iofull=$v;;
      memavail) memavail=$v;; swapused) swapused=$v;; oomkill) oomkill=$v;; rootfree) rootfree=$v;;
    esac
  done <<< "$out"
  [[ -n $nproc && -n $load1 && -n $memsome && -n $iofull && -n $memavail && -n $swapused && -n $oomkill && -n $rootfree ]] || {
    echo 'probe incomplete'; return 1;
  }
  local number
  for number in "$nproc" "$memavail" "$swapused" "$oomkill" "$rootfree"; do
    [[ $number =~ ^[0-9]+$ ]] || { echo 'probe contains non-numeric data'; return 1; }
  done
  for number in "$load1" "$memsome" "$iofull"; do
    [[ $number =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo 'probe contains non-numeric data'; return 1; }
  done
  (( nproc > 0 )) || { echo 'probe reports zero processors'; return 1; }
  local prev_swap='' prev_oom='' reason=''
  [[ -f $Q/$HOST.probe ]] && { prev_swap=$(sed -n 1p "$Q/$HOST.probe"); prev_oom=$(sed -n 2p "$Q/$HOST.probe"); }
  printf '%s\n%s\n' "$swapused" "$oomkill" > "$Q/$HOST.probe"
  if awk -v a="$memsome" -v l="$PSI_LIMIT" 'BEGIN{exit !(a>l)}'; then reason="memory PSI some avg10=$memsome>$PSI_LIMIT"
  elif awk -v a="$iofull" -v l="$PSI_LIMIT" 'BEGIN{exit !(a>l)}'; then reason="io PSI full avg10=$iofull>$PSI_LIMIT"
  elif [[ -n $prev_oom ]] && (( oomkill > prev_oom )); then reason="OOM kill counter rose $prev_oom->$oomkill"
  elif [[ -n $prev_swap ]] && (( swapused - prev_swap >= SWAP_GROW_MB )); then reason="swap in use grew ${prev_swap}->${swapused} MB"
  elif (( swapused > SWAP_ABS_MB )); then reason="swap in use ${swapused} MB>$SWAP_ABS_MB"
  elif (( memavail < MEM_AVAIL_MIN_MB )); then reason="MemAvailable ${memavail} MB<$MEM_AVAIL_MIN_MB"
  elif (( rootfree < ROOT_FREE_MIN_GB )); then reason="root disk free ${rootfree} GB<$ROOT_FREE_MIN_GB"
  elif awk -v a="$load1" -v n="$nproc" -v f="$LOAD_FACTOR" 'BEGIN{exit !(a>=n*f)}'; then
    reason="load1=$load1 >= $(awk -v n="$nproc" -v f="$LOAD_FACTOR" 'BEGIN{print n*f}') (heavy job headroom)"
  fi
  [[ -z $reason ]] && return 0
  echo "$reason"; return 1
}

flag_set() { [[ -e $BASE/host-sat-queue.disabled || -e $BASE/host-sat-queue.disabled.$HOST ]]; }

record() {
  ( flock 8; printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@" >> "$RESULTS" ) 8>>"$RESULTS.lock"
}

write_auto_pause() {
  local flag=$BASE/host-sat-queue.disabled.$HOST at tmp why
  at=$(now)
  tmp=$(mktemp "$flag.tmp.XXXXXX") || { log 'could not create pause metadata temp file'; return 1; }
  if ! {
    printf 'host-sat queue self-pause\n'
    printf 'by=queue-auto\n'
    printf 'host=%s\n' "$HOST"
    printf 'at=%s\n' "$at"
    printf 'streak=%s consecutive INFRA results (limit %s)\n' "$consec_infra" "$MAX_CONSEC_INFRA"
    printf 'last_lane=%s\n' "${LAST_LANE:-unknown}"
    printf 'last_result=%s\n' "${LAST_RESULT:-unknown}"
    printf 'last_run=%s\n' "${LAST_RUN:-unknown}"
    printf 'log=%s\n' "${LAST_LOG:-unknown}"
    printf 'fix=inspect the log, repair the failed prerequisite, verify the host can launch, then run: %s resume %s\n' "$0" "$HOST"
  } > "$tmp"; then
    rm -f -- "$tmp"; log "could not write pause metadata to $tmp"; return 1
  fi
  if ! mv -f -- "$tmp" "$flag"; then rm -f -- "$tmp"; log "could not install pause metadata at $flag"; return 1; fi
  why="queue-auto streak=$consec_infra last_result=${LAST_RESULT:-unknown} at=$at"
  record "$at" "$HOST" "${LAST_LANE:-unknown}" '-' "${LAST_RUN:-unknown}" "PAUSED:$why" 0 "${LAST_LOG:-unknown}"
  log "auto-paused; metadata written to $flag"
}

lane_log_path() { echo "$BASE/logs/host-sat-$1.log"; }

queue_hosts() {
  local hosts host lanes
  hosts=$(controller_host_list) || return 2
  while IFS= read -r host; do
    [[ -n $host ]] || continue
    lanes=$(controller_host_lanes "$host") || return 2
    [[ -n $lanes ]] && printf '%s\n' "$host"
  done <<< "$hosts"
}

run_one() {
  local pdir=$1 lane=$2 ts name sha wt rg start rc secs verdict res
  LAST_VERDICT=INFRA; LAST_LANE=''; LAST_RESULT=''; LAST_RUN=''; LAST_LOG=''
  controller_host_api_check "$HOST" >/dev/null 2>&1 || {
    log 'host API identity check failed; not launching'; LAST_VERDICT=SKIP; LAST_RESULT=SKIP; return;
  }
  git -C "$REPO" fetch -q origin '+refs/heads/main:refs/remotes/origin/main' 2>>"$Q/$HOST.log" || log 'fetch warning'
  git -C "$REPO" fetch -q --tags origin 2>>"$Q/$HOST.log" || true
  sha=$(git -C "$REPO" rev-parse origin/main) || { LAST_VERDICT=SKIP; return; }
  ts=$(date -u +%Y%m%dT%H%M%S)
  name="sat-$(echo "$lane" | tr -c 'A-Za-z0-9\n' -)-$ts-$HOST"; name=${name:0:60}
  wt=$REPO/.worktrees/hostsat-q-$HOST-$ts
  git -C "$REPO" worktree add -q --detach "$wt" "$sha" 2>>"$Q/$HOST.log" || { log 'worktree add failed'; LAST_VERDICT=SKIP; return; }
  rg=$Q/remote-gate-$HOST.py
  cp "$wt/scripts/netcup/runner/remote-gate.py" "$rg"
  start=$(date +%s); printf '%s %s %s %s\n' "$name" "$lane" "$sha" "$(now)" > "$Q/$HOST.current"
  local lg; lg=$(lane_log_path "$name")
  log "launch $name lane=$pdir:$lane commit=${sha:0:12}"
  ( cd "$wt" && REMOTE_GATE_HOSTS=$REMOTE_GATE_HOSTS nohup "$PY" "$rg" run "$HOST" . "$lane" \
      --project-dir "$pdir" --repo "${REMOTE_GATE_REPO_NAME:-vbpub}" --name "$name" --detach --wait-psi 120 >"$lg" 2>&1 )
  rc=$?
  git -C "$REPO" worktree remove --force "$wt" 2>>"$Q/$HOST.log" || log "worktree remove failed: $wt"
  if [[ $rc -ne 0 ]] || ! grep -q '^started:' "$lg"; then
    secs=$(( $(date +%s) - start )); log "launch failed rc=$rc: $(tail -1 "$lg" | cut -c1-160)"
    LAST_LANE="$pdir:$lane"; LAST_RESULT=INFRA:LAUNCH_FAILED; LAST_RUN=$name; LAST_LOG=$lg
    record "$(now)" "$HOST" "$LAST_LANE" "${sha:0:12}" "$name" "$LAST_RESULT" "$secs" "$lg"
    rm -f -- "$Q/$HOST.current"; return
  fi
  local st cancelled=0
  while :; do
    sleep "$POLL_S"
    st=$(REMOTE_GATE_HOSTS=$REMOTE_GATE_HOSTS "$PY" "$rg" status "$name" 2>&1 | head -1)
    case $st in *finished*) break;; esac
    if (( $(date +%s) - start > LANE_TIMEOUT_S && cancelled == 0 )); then
      log "timeout: cancelling own run $name"
      REMOTE_GATE_HOSTS=$REMOTE_GATE_HOSTS "$PY" "$rg" cancel "$name" >>"$lg" 2>&1; cancelled=1
    fi
    (( $(date +%s) - start <= LANE_TIMEOUT_S + 600 )) || { st=timeout-no-finish; break; }
  done
  secs=$(( $(date +%s) - start ))
  if [[ $st == *finished* ]]; then
    REMOTE_GATE_HOSTS=$REMOTE_GATE_HOSTS "$PY" "$rg" collect "$name" >>"$lg" 2>&1
    res=$BASE/remote-gates/$name/result.json
    verdict=$([[ -f $res ]] && "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$res" || echo UNKNOWN)
    case $verdict in PASS|FAIL) ;; *) verdict="INFRA:$verdict";; esac
  else verdict=INFRA:NO_FINISH; fi
  LAST_LANE="$pdir:$lane"; LAST_RESULT=$verdict; LAST_RUN=$name; LAST_LOG=$lg
  record "$(now)" "$HOST" "$LAST_LANE" "${sha:0:12}" "$name" "$verdict" "$secs" "$lg"
  echo "$verdict $secs" > "$Q/runs/$name.done"
  rm -f -- "$Q/$HOST.current"
  log "done $name verdict=$verdict seconds=$secs"
  LAST_VERDICT=${verdict%%:*}
}

loop() {
  HOST=$1
  local lane_text idx=0 consec_infra=0 last_reason='' last_reason_t=0 reason entry
  lane_text=$(controller_host_lanes "$HOST") || exit 2
  local -a mine=()
  mapfile -t mine <<< "$lane_text"
  local -a configured=()
  for entry in "${mine[@]}"; do [[ -n $entry ]] && configured+=("$entry"); done
  (( ${#configured[@]} > 0 )) || { echo "no queue_lanes configured for $HOST" >&2; exit 2; }
  mkdir -p -- "$Q/runs"
  exec 9>"$Q/$HOST.lock"; flock -n 9 || { echo "queue for $HOST already running" >&2; exit 1; }
  echo $$ > "$Q/$HOST.pid"
  trap 'log "queue loop stopping (SIGTERM); in-flight remote run, if any, is left to finish"; rm -f -- "$Q/$HOST.pid"; exit 0' TERM INT
  [[ -f $RESULTS ]] || printf 'utc\thost\tlane\tcommit\trun_name\tverdict\tseconds\tlog\n' > "$RESULTS"
  [[ ! -f $Q/$HOST.idx ]] || idx=$(<"$Q/$HOST.idx")
  [[ $idx =~ ^[0-9]+$ ]] || { echo "invalid queue index for $HOST" >&2; exit 2; }
  log "queue loop started pid=$$ lanes=${#configured[@]} idx=$idx"
  while :; do
    if flag_set; then
      [[ $last_reason == disabled ]] || log 'disabled flag present: not launching'
      last_reason=disabled; sleep 60; continue
    fi
    if reason=$(check_ok); then
      last_reason=''
      entry=${configured[$((idx % ${#configured[@]}))]}
      idx=$((idx + 1)); echo "$idx" > "$Q/$HOST.idx"
      IFS='|' read -r pdir lane <<< "$entry"
      [[ -n $pdir && -n $lane ]] || { log "invalid queue lane: $entry"; sleep 60; continue; }
      run_one "$pdir" "$lane"
      case $LAST_VERDICT in
        INFRA)
          consec_infra=$((consec_infra + 1)); log "INFRA result #$consec_infra in a row; backing off ${INFRA_BACKOFF_S}s"
          if (( consec_infra >= MAX_CONSEC_INFRA )); then
            log "too many INFRA results; writing pause metadata to $BASE/host-sat-queue.disabled.$HOST"
            write_auto_pause || { log 'pause metadata write failed; touching empty flag'; touch "$BASE/host-sat-queue.disabled.$HOST"; }
          fi
          sleep "$INFRA_BACKOFF_S" ;;
        PASS|FAIL) consec_infra=0; sleep 30 ;;
        *) sleep 60 ;;
      esac
    else
      if [[ $reason != "$last_reason" ]] || (( $(date +%s) - last_reason_t > 1800 )); then
        log "skip+wait: $reason"; last_reason=$reason; last_reason_t=$(date +%s)
      fi
      sleep "$RECHECK_S"
    fi
  done
}

status() {
  local hosts host pid
  hosts=$(controller_host_list) || return 2
  while IFS= read -r host; do
    [[ -n $host ]] || continue
    pid=$(cat "$Q/$host.pid" 2>/dev/null || true); alive=dead
    [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null && alive="alive pid=$pid"
    echo "== $host: queue $alive; next lane idx=$(cat "$Q/$host.idx" 2>/dev/null || echo 0)"
    [[ ! -e $Q/$host.current ]] || echo "   in flight: $(cat "$Q/$host.current")"
    for flag in "$BASE/host-sat-queue.disabled" "$BASE/host-sat-queue.disabled.$host"; do
      [[ -e $flag ]] || continue
      label=host; [[ $flag == "$BASE/host-sat-queue.disabled" ]] && label=GLOBAL
      echo "   $label disabled flag present: $flag"
      if [[ -s $flag ]]; then sed 's/^/     /' "$flag"; else echo '     pause metadata: absent (legacy empty flag)'; fi
    done
    echo '   last log:'; tail -n 3 "$Q/$host.log" 2>/dev/null | sed 's/^/     /'
    echo '   last results:'
    awk -F '\t' -v h="$host" 'NR>1 && $2==h{print "     "$1" "$3" "$4" "$6" "$7"s"}' "$RESULTS" 2>/dev/null | tail -n 5
  done <<< "$hosts"
}

if [[ ${BASH_SOURCE[0]} != "$0" ]]; then return 0; fi
cmd=${1:-status}; shift || true
case $cmd in
  run)
    [[ $# == 1 ]] || { echo 'run requires one configured host alias' >&2; exit 2; }
    loop "$1" ;;
  probe)
    [[ $# == 1 ]] || { echo 'probe requires one configured host alias' >&2; exit 2; }
    controller_host_api_check "$1" >/dev/null || { echo 'host API identity check failed' >&2; exit 97; }
    probe "$1" ;;
  start)
    hosts=()
    if (( $# )); then hosts=("$@"); else
      host_text=$(queue_hosts) || exit 2
      [[ -z $host_text ]] || mapfile -t hosts <<< "$host_text"
    fi
    for host in "${hosts[@]}"; do
      lanes=$(controller_host_lanes "$host") || exit 2
      [[ -n $lanes ]] || { echo "no queue_lanes configured for $host" >&2; exit 2; }
      nohup setsid bash "$0" run "$host" >> "$Q/$host.out" 2>&1 < /dev/null &
      echo "started queue for $host (log $Q/$host.log)"
    done ;;
  stop)
    hosts=()
    if (( $# )); then hosts=("$@"); else
      host_text=$(controller_host_list) || exit 2
      [[ -z $host_text ]] || mapfile -t hosts <<< "$host_text"
    fi
    for host in "${hosts[@]}"; do
      if ! controller_host_scalar "$host" guard >/dev/null; then
        echo "unknown or invalid configured host: $host" >&2
        exit 2
      fi
      pid=$(cat "$Q/$host.pid" 2>/dev/null || true)
      [[ -n $pid ]] && kill -TERM "$pid" 2>/dev/null && echo "stopped queue loop $host ($pid)" || echo "$host: not running"
    done ;;
  pause)
    if (( $# )); then for host in "$@"; do : "$(controller_host_lanes "$host")" || exit 2; touch "$BASE/host-sat-queue.disabled.$host"; done
    else touch "$BASE/host-sat-queue.disabled"; fi
    echo 'paused' ;;
  resume)
    if (( $# )); then for host in "$@"; do : "$(controller_host_lanes "$host")" || exit 2; rm -f -- "$BASE/host-sat-queue.disabled.$host"; done
    else rm -f -- "$BASE/host-sat-queue.disabled"; fi
    echo 'resumed' ;;
  status) status ;;
  *) sed -n '2,5p' "$0"; exit 2 ;;
esac
