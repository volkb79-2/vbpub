#!/usr/bin/env bash
# container-mempress.sh — stepped memory.high squeeze for an arbitrary Docker
# container's cgroup scope, to measure its real (hot) working set under
# pressure. Generic sibling of soulmask-mempress.sh: that one knows Soulmask
# instances and game/pak slices; this one targets any container by name and
# adds per-step monitoring, stop thresholds, and a JSONL log for later
# analysis (e.g. sizing interactive.slice from a devcontainer squeeze).
#
# Protocol: start memory.high just above current usage, lower it by --step
# every --delay seconds. Each step samples memory.current/anon/file/zswap,
# per-cgroup PSI, and the anon refault rate. Squeezing stops when a pressure
# signal crosses its threshold (PSI some/full avg10, refault/s) or --floor is
# reached; memory.high is then restored to --relax-to. The last high value
# BEFORE the stop trigger is reported as the squeeze point ≈ hot+warm set.
#
# Always restores memory.high on exit (also on Ctrl-C). Refuses containers
# whose scope has memory.min > 0 (prod floors, e.g. Soulmask) unless --force.
#
# Usage:
#   container-mempress.sh <container-name-or-prefix> [options]
# Options:
#   --step SIZE        step size (default 256M)
#   --delay SEC        settle time per step (default 15; PSI avg10 window is 10s)
#   --floor SIZE       never set memory.high below this (default 1G)
#   --start SIZE       initial memory.high (default: usage rounded up to step)
#   --relax-to VAL     memory.high restored on exit: SIZE or "max" (default max)
#   --psi-some LIMIT   stop when memory PSI some avg10 > LIMIT %  (default 10)
#   --psi-full LIMIT   stop when memory PSI full avg10 > LIMIT %  (default 5)
#   --rf-limit N       stop when anon refaults/s > N              (default 200)
#   --log FILE         JSONL log (default /var/log/mempress/<name>-<ts>.jsonl)
#   --force            allow a target with memory.min > 0
#
# Example (devcontainer working-set measurement, run as root on the host):
#   container-mempress.sh dstdns-devcontainer-vb
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "must run as root"

to_bytes() { # 256M / 1G / 4096 -> bytes
    local v=$1
    case $v in
        *G|*g) echo $(( ${v%[Gg]} * 1024 * 1024 * 1024 )) ;;
        *M|*m) echo $(( ${v%[Mm]} * 1024 * 1024 )) ;;
        *K|*k) echo $(( ${v%[Kk]} * 1024 )) ;;
        *[0-9]) echo "$v" ;;
        *) die "cannot parse size: $v" ;;
    esac
}
mib() { echo $(( $1 / 1024 / 1024 )); }

TARGET=""; STEP=$(to_bytes 256M); DELAY=15; FLOOR=$(to_bytes 1G)
START=""; RELAX_TO="max"; PSI_SOME=10; PSI_FULL=5; RF_LIMIT=200
LOG=""; FORCE=0
while [ $# -gt 0 ]; do
    case $1 in
        --step)     STEP=$(to_bytes "$2"); shift 2 ;;
        --delay)    DELAY=$2; shift 2 ;;
        --floor)    FLOOR=$(to_bytes "$2"); shift 2 ;;
        --start)    START=$(to_bytes "$2"); shift 2 ;;
        --relax-to) [ "$2" = max ] && RELAX_TO=max || RELAX_TO=$(to_bytes "$2"); shift 2 ;;
        --psi-some) PSI_SOME=$2; shift 2 ;;
        --psi-full) PSI_FULL=$2; shift 2 ;;
        --rf-limit) RF_LIMIT=$2; shift 2 ;;
        --log)      LOG=$2; shift 2 ;;
        --force)    FORCE=1; shift ;;
        -h|--help)  sed -n '2,33p' "$0"; exit 0 ;;
        -*)         die "unknown option: $1" ;;
        *)          [ -n "$TARGET" ] && die "one container only"; TARGET=$1; shift ;;
    esac
done
[ -n "$TARGET" ] || die "container name/prefix required (see --help)"

# --- resolve container -> cgroup scope -------------------------------------
CID=$(docker ps -q --filter "name=$TARGET" | head -1)
[ -n "$CID" ] || die "no running container matches name filter '$TARGET'"
FULL_ID=$(docker inspect -f '{{.Id}}' "$CID")
NAME=$(docker inspect -f '{{.Name}}' "$CID"); NAME=${NAME#/}
CG=$(find /sys/fs/cgroup -maxdepth 3 -type d -name "docker-${FULL_ID}.scope" | head -1)
[ -n "$CG" ] && [ -f "$CG/memory.high" ] || die "cgroup scope for $NAME not found"

CUR_MIN=$(cat "$CG/memory.min")
if [ "$CUR_MIN" != 0 ] && [ "$FORCE" -ne 1 ]; then
    die "$NAME has memory.min=$CUR_MIN (a protected/prod workload?) — refusing without --force"
fi
ORIG_HIGH=$(cat "$CG/memory.high")

[ -n "$LOG" ] || { mkdir -p /var/log/mempress; LOG="/var/log/mempress/${NAME}-$(date +%Y%m%d_%H%M%S).jsonl"; }

restore() {
    echo "$RELAX_TO" > "$CG/memory.high" 2>/dev/null || true
    echo "[mempress] memory.high restored to $RELAX_TO" >&2
}
trap restore EXIT
trap 'exit 130' INT TERM   # -> EXIT trap runs restore; without this, bash would resume the loop after the handler

# --- samplers ---------------------------------------------------------------
stat_field() { awk -v k="$1" '$1==k {print $2; found=1} END {if (!found) print 0}' "$CG/memory.stat"; }
psi_avg10()  { awk -v w="$1" '$1==w {sub(/^avg10=/,"",$2); print $2}' "$CG/memory.pressure"; }
events_high(){ awk '$1=="high" {print $2}' "$CG/memory.events"; }

sample() { # -> globals S_*
    S_CURRENT=$(cat "$CG/memory.current")
    S_ANON=$(stat_field anon)
    S_FILE=$(stat_field file)
    S_ZSWAPPED=$(stat_field zswapped)
    S_ZPOOL=$(cat "$CG/memory.zswap.current" 2>/dev/null || echo 0)
    S_SWAP=$(cat "$CG/memory.swap.current" 2>/dev/null || echo 0)
    S_RF=$(stat_field workingset_refault_anon)
    S_PSI_SOME=$(psi_avg10 some)
    S_PSI_FULL=$(psi_avg10 full)
    S_EV_HIGH=$(events_high)
}

log_step() { # $1=step_idx $2=high $3=rf_per_s
    printf '{"type":"step","ts":"%s","container":"%s","step":%d,"high":%s,"current":%s,"anon":%s,"file":%s,"zswapped":%s,"z_pool":%s,"swap":%s,"rf_cum":%s,"rf_per_s":%s,"psi_some10":%s,"psi_full10":%s,"events_high":%s}\n' \
        "$(date -Is)" "$NAME" "$1" "$2" "$S_CURRENT" "$S_ANON" "$S_FILE" \
        "$S_ZSWAPPED" "$S_ZPOOL" "$S_SWAP" "$S_RF" "$3" "$S_PSI_SOME" "$S_PSI_FULL" "$S_EV_HIGH" >> "$LOG"
}

# --- run --------------------------------------------------------------------
sample
if [ -n "$START" ]; then HIGH=$START
else HIGH=$(( (S_CURRENT / STEP + 1) * STEP ))    # round usage up to step boundary
fi
echo "[mempress] target: $NAME ($CID)" >&2
echo "[mempress] cgroup: $CG" >&2
echo "[mempress] usage now: $(mib "$S_CURRENT")M   start high: $(mib "$HIGH")M   step: $(mib "$STEP")M   floor: $(mib "$FLOOR")M" >&2
echo "[mempress] stop at: PSI some>${PSI_SOME}% or full>${PSI_FULL}% or refaults>${RF_LIMIT}/s" >&2
echo "[mempress] log: $LOG   (Ctrl-C is safe: memory.high restores to $RELAX_TO)" >&2
printf '{"type":"header","ts":"%s","container":"%s","cgroup":"%s","orig_high":"%s","step":%s,"delay":%s,"floor":%s,"psi_some_limit":%s,"psi_full_limit":%s,"rf_limit":%s}\n' \
    "$(date -Is)" "$NAME" "$CG" "$ORIG_HIGH" "$STEP" "$DELAY" "$FLOOR" "$PSI_SOME" "$PSI_FULL" "$RF_LIMIT" >> "$LOG"

STEP_IDX=0; PREV_RF=$S_RF; SQUEEZE_POINT=$HIGH; STOP_REASON="floor"
while [ "$HIGH" -ge "$FLOOR" ]; do
    echo "$HIGH" > "$CG/memory.high"
    sleep "$DELAY"
    sample
    RF_RATE=$(( (S_RF - PREV_RF) / DELAY )); PREV_RF=$S_RF
    log_step "$STEP_IDX" "$HIGH" "$RF_RATE"
    echo "[mempress] step $STEP_IDX: high=$(mib "$HIGH")M current=$(mib "$S_CURRENT")M anon=$(mib "$S_ANON")M z_eq=$(mib "$S_ZSWAPPED")M rf=${RF_RATE}/s psi=${S_PSI_SOME}/${S_PSI_FULL}" >&2

    over_some=$(awk -v a="$S_PSI_SOME" -v l="$PSI_SOME" 'BEGIN{print (a>l)?1:0}')
    over_full=$(awk -v a="$S_PSI_FULL" -v l="$PSI_FULL" 'BEGIN{print (a>l)?1:0}')
    if [ "$over_some" = 1 ] || [ "$over_full" = 1 ] || [ "$RF_RATE" -gt "$RF_LIMIT" ]; then
        [ "$over_some" = 1 ] && STOP_REASON="psi_some"
        [ "$over_full" = 1 ] && STOP_REASON="psi_full"
        [ "$RF_RATE" -gt "$RF_LIMIT" ] && STOP_REASON="refault_rate"
        break
    fi
    SQUEEZE_POINT=$HIGH        # last high that showed no pressure
    HIGH=$(( HIGH - STEP )); STEP_IDX=$(( STEP_IDX + 1 ))
done

printf '{"type":"summary","ts":"%s","container":"%s","stop_reason":"%s","stop_high":%s,"squeeze_point":%s,"current_at_stop":%s,"anon_at_stop":%s,"zswapped_at_stop":%s,"z_pool_at_stop":%s,"relaxed_to":"%s"}\n' \
    "$(date -Is)" "$NAME" "$STOP_REASON" "$HIGH" "$SQUEEZE_POINT" "$S_CURRENT" "$S_ANON" "$S_ZSWAPPED" "$S_ZPOOL" "$RELAX_TO" >> "$LOG"
echo "[mempress] DONE: stop_reason=$STOP_REASON  squeeze point (hot+warm set) ≈ $(mib "$SQUEEZE_POINT")M  (stopped at high=$(mib "$HIGH")M)" >&2
echo "[mempress] summary + per-step data in: $LOG" >&2
