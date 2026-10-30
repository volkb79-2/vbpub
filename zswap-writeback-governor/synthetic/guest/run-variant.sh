#!/usr/bin/env bash
# Run ONE variant of the zswap writeback-governor synthetic.
# Runs INSIDE the VM-harness guest as root, after setup-guest.sh, with the QEMU
# disk write throttle already applied by the host-side driver (run-synthetic.sh).
# NEVER run on the live host.
#
#   run-variant.sh <variant> <outdir>
#
# Variants (see ../README.md for what each isolates):
#   base               constant low global pressure, steady band, shrinker on        (H1)
#   idle               pressure only while filling the pool, none after settle        (H1 null)
#   noshrink           base with zswap shrinker_enabled=N                               (control)
#   squeeze            base + min/low/high step down at settle (the Wings squeeze)     (H2)
#   squeeze-minlow     base + only min/low step down at settle                          (H2)
#   wsf200             base with vm.watermark_scale_factor=200                         (H3)
#   highonly           no global pressure; memory.high alone holds the pool            (H4)
#   highonly-noshrink  highonly with shrinker_enabled=N                                (H4 control)
#
# Every number that can be derived from the machine is derived (MemTotal, watermarks,
# the settled usage U); the few policy knobs are env-overridable and are all written to
# meta.json so a result is never separated from its parameters.
set -euo pipefail

VARIANT="${1:?usage: run-variant.sh <variant> <outdir>}"
OUT="${2:?usage: run-variant.sh <variant> <outdir>}"
HERE="$(cd "$(dirname "$0")" && pwd)"

WS_MB="${WS_MB:-1400}"                # game working set (anon, compressible ~2.5x)
HOT_PCT="${HOT_PCT:-10}"
COLD_PUSHED_PCT="${COLD_PUSHED_PCT:-30}"   # host: ~780M pool holds ~2.0G logical of ~6.6G anon = 30%
RATIO_X10="${RATIO_X10:-25}"          # expected compression ratio x10 (sizing only; measured ratio is in samples)
CHURN_MB="${CHURN_MB:-24}"            # bg churn depth
CHURN_PERIOD_S="${CHURN_PERIOD_S:-5}"
GAME_CHURN_MB="${GAME_CHURN_MB:-8}"
CAP_S="${CAP_S:-2400}"                # max seconds after settle
FILL_TIMEOUT_S="${FILL_TIMEOUT_S:-1800}"
STALL_MS="${STALL_MS:-200}"
THROTTLE_MIBPS="${THROTTLE_MIBPS:-unset}"   # recorded only; applied by the driver

die() { echo "run-variant: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "must run as root"
# shellcheck source=lib-guard.sh
. "$HERE/lib-guard.sh"
require_harness_guest   # exits 97 before any sysctl or cgroup write unless run-synthetic.sh marked this guest
[ -d /sys/fs/cgroup/zwg/game ] || die "run setup-guest.sh first"
mountpoint -q /sys/kernel/debug || die "debugfs not mounted; run setup-guest.sh first"

BG=1 BG_KEEP=1 WSF=50 SHRINKER=Y SQUEEZE=none HIGHMODE=loose
case "$VARIANT" in
    base) ;;
    idle) BG_KEEP=0 ;;
    noshrink) SHRINKER=N ;;
    squeeze) SQUEEZE=all ;;
    squeeze-minlow) SQUEEZE=minlow ;;
    wsf200) WSF=200 ;;
    highonly) BG=0 HIGHMODE=tight ;;
    highonly-noshrink) BG=0 HIGHMODE=tight SHRINKER=N ;;
    *) die "unknown variant '$VARIANT'" ;;
esac

mkdir -p "$OUT"
CG=/sys/fs/cgroup/zwg
MB=1048576
meminfo_kb() { awk -v k="$1:" '$1==k {print $2}' /proc/meminfo; }

# --- derived machine facts -------------------------------------------------
MEMTOTAL_MB=$(( $(meminfo_kb MemTotal) / 1024 ))
BASELINE_USED_MB=$(( ( $(meminfo_kb MemTotal) - $(meminfo_kb MemAvailable) ) / 1024 ))   # kernel + userland before our load
# min_free_kbytes: host 67584 KiB on 16 GiB, scaled by RAM, so the watermark geometry is the host's proportion.
MIN_FREE_KB=$(( 67584 * $(meminfo_kb MemTotal) / 16777216 ))
sysctl -q -w vm.min_free_kbytes="$MIN_FREE_KB" vm.watermark_scale_factor="$WSF"
[ "$(sysctl -n vm.watermark_scale_factor)" = "$WSF" ] || die "watermark_scale_factor did not stick"
HIGH_TOTAL_MB=$(awk '$1=="high" {s+=$2} END {printf "%d", s*4/1024}' /proc/zoneinfo)   # pages -> MiB
LOW_TOTAL_MB=$(awk '$1=="low" {s+=$2} END {printf "%d", s*4/1024}' /proc/zoneinfo)
FREE_BASE_MB=$(( HIGH_TOTAL_MB + 4 ))   # baseline free just above the high watermark: kswapd asleep until churn dips it

echo "$SHRINKER" > /sys/module/zswap/parameters/shrinker_enabled
[ "$(cat /sys/module/zswap/parameters/shrinker_enabled)" = "$SHRINKER" ] || die "shrinker_enabled did not stick"

# game_target = settled resident+compressed footprint when COLD_PUSHED_PCT of the WS sits in the pool
GAME_TARGET_MB=$(( WS_MB * (100 - COLD_PUSHED_PCT) / 100 + WS_MB * COLD_PUSHED_PCT * 10 / (100 * RATIO_X10) ))
BALLAST_MB=0
if [ "$BG" = 1 ]; then
    BALLAST_MB=$(( MEMTOTAL_MB - BASELINE_USED_MB - GAME_TARGET_MB - FREE_BASE_MB ))
    BALLAST_MB=$(( BALLAST_MB / 16 * 16 ))
    [ "$BALLAST_MB" -ge 64 ] || die "derived ballast $BALLAST_MB MiB is too small: guest RAM ($MEMTOTAL_MB MiB) cannot host this working set ($WS_MB MiB); raise --mem or lower WS_MB"
fi

# --- cgroup configuration --------------------------------------------------
set_cg() { echo "$2" > "$CG/$1" || die "cannot write $2 to $CG/$1"; }
set_band() {  # min_mib low_mib high_mib (a value of 'max' for high)
    set_cg game/memory.min "$(( $1 * MB ))"
    set_cg game/memory.low "$(( $2 * MB ))"
    if [ "$3" = max ]; then set_cg game/memory.high max; else set_cg game/memory.high "$(( $3 * MB ))"; fi
    set_parent_protection
}
# The parent must itself carry protection covering its children, or the kernel's effective
# protection for the children is clamped to the parent's (page_counter.c effective_protection(),
# overcommit branch: protected * parent_effective / siblings_protected, and zwg's own emin/elow
# would be 0). Called after every change of the children's min/low.
set_parent_protection() {
    local gmin glow bmin
    gmin=$(cat "$CG/game/memory.min"); glow=$(cat "$CG/game/memory.low"); bmin=$(cat "$CG/bg/memory.min")
    set_cg memory.min "$(( gmin + bmin ))"
    set_cg memory.low "$(( glow + bmin ))"
}
set_cg game/memory.zswap.writeback 1
set_cg game/memory.zswap.max max
if [ "$HIGHMODE" = tight ]; then
    # memory.high alone holds the settled footprint; protection is irrelevant to target reclaim (KERNEL-ANALYSIS section 6)
    set_band 0 0 "$GAME_TARGET_MB"
else
    set_band 0 0 max      # unprotected, unbounded while the game fills; PRE band is applied at fill-done
fi
set_cg bg/memory.zswap.writeback 0   # keep the ballast out of the writeback counters

# --- start observation ------------------------------------------------------
rm -f "$OUT"/fill-done "$OUT"/ready "$OUT"/bg-ready "$OUT"/done "$OUT"/settled.json "$OUT"/burst.json "$OUT"/never-settled
python3 "$HERE/sampler.py" --out "$OUT" --cg "$CG/game" --fill-done-file "$OUT/fill-done" \
    --cap-s "$CAP_S" --post-s 300 --max-fill-wait-s "${SETTLE_TIMEOUT_S:-900}" --bg-cg "$CG/bg" > "$OUT/sampler.log" 2>&1 &
SAMPLER=$!
cleanup() {
    [ -n "${WORKLOAD:-}" ] && kill "$WORKLOAD" 2>/dev/null || true
    [ -n "${BGPID:-}" ] && kill "$BGPID" 2>/dev/null || true
    kill "$SAMPLER" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT
sleep 3
kill -0 "$SAMPLER" 2>/dev/null || die "sampler died at start: $(cat "$OUT/sampler.log")"

# --- start the game ---------------------------------------------------------
bash -c 'echo $$ > '"$CG"'/game/cgroup.procs; exec python3 '"$HERE"'/workload.py \
    --ws-mb '"$WS_MB"' --hot-pct '"$HOT_PCT"' --churn-mb '"$GAME_CHURN_MB"' \
    --probe-file '"$OUT"'/probe.jsonl --ready-file '"$OUT"'/ready' > "$OUT/workload.log" 2>&1 &
WORKLOAD=$!
for _ in $(seq 1 "$FILL_TIMEOUT_S"); do
    [ -f "$OUT/ready" ] && break
    kill -0 "$WORKLOAD" 2>/dev/null || die "workload died: $(cat "$OUT/workload.log")"
    sleep 1
done
[ -f "$OUT/ready" ] || die "workload not ready after ${FILL_TIMEOUT_S}s"

# --- push the cold part into zswap ------------------------------------------
if [ "$BG" = 1 ]; then
    bash -c 'echo $$ > '"$CG"'/bg/cgroup.procs; exec python3 '"$HERE"'/bgpressure.py \
        --ballast-mb '"$BALLAST_MB"' --churn-mb '"$CHURN_MB"' --period-s '"$CHURN_PERIOD_S"' \
        --ready-file '"$OUT"'/bg-ready' > "$OUT/bg.log" 2>&1 &
    BGPID=$!
    set_cg bg/memory.min "$(( BALLAST_MB * MB ))"
    set_parent_protection
    for _ in $(seq 1 "$FILL_TIMEOUT_S"); do
        [ -f "$OUT/bg-ready" ] && break
        kill -0 "$BGPID" 2>/dev/null || die "bgpressure died: $(cat "$OUT/bg.log")"
        sleep 1
    done
    [ -f "$OUT/bg-ready" ] || die "ballast not ready after ${FILL_TIMEOUT_S}s"
fi

# wait for the game's footprint to come down to the target (reclaim has pushed the cold pages out)
reached=0
for _ in $(seq 1 "$FILL_TIMEOUT_S"); do
    cur_mb=$(( $(cat $CG/game/memory.current) / MB ))
    if [ "$cur_mb" -le $(( GAME_TARGET_MB * 105 / 100 )) ]; then reached=1; break; fi
    kill -0 "$WORKLOAD" 2>/dev/null || die "workload died during fill: $(cat "$OUT/workload.log")"
    sleep 1
done
[ "$reached" = 1 ] || die "game memory.current ($cur_mb MiB) never reached the target ($GAME_TARGET_MB MiB) in ${FILL_TIMEOUT_S}s; variant sizing invalid"

U_MB=$(( $(cat $CG/game/memory.current) / MB ))
# PRE band (loose modes): fully protected like the host before the squeeze; high not binding
if [ "$HIGHMODE" != tight ]; then
    set_band $(( U_MB * 95 / 100 )) $(( U_MB * 95 / 100 )) $(( U_MB * 2 ))
fi
if [ "$BG" = 1 ] && [ "$BG_KEEP" = 0 ]; then
    # idle control: the pool is populated; remove all background pressure before settle
    kill "$BGPID"; wait "$BGPID" 2>/dev/null || true; BGPID=""
    set_cg bg/memory.min 0
    set_parent_protection
fi
date +%s.%N > "$OUT/fill-done"

# --- wait for settle, optionally squeeze, wait for done ---------------------
OUTCOME=ok
SQUEEZED_AT=""
while [ ! -f "$OUT/settled.json" ]; do
    if [ -f "$OUT/never-settled" ]; then OUTCOME=did-not-settle; break; fi
    kill -0 "$SAMPLER" 2>/dev/null || die "sampler exited before settle: $(cat "$OUT/sampler.log")"
    sleep 1
done
# "did-not-settle" is a recorded outcome (the pool kept moving for SETTLE_TIMEOUT_S after the fill),
# not an error: analyze.py reports it separately from "no burst" and never counts it as one.
if [ "$OUTCOME" = ok ]; then
    case "$SQUEEZE" in
        all)    set_band $(( U_MB * 53 / 100 )) $(( U_MB * 67 / 100 )) $(( U_MB * 107 / 100 )); SQUEEZED_AT=$(date +%s.%N) ;;
        minlow) set_band $(( U_MB * 53 / 100 )) $(( U_MB * 67 / 100 )) $(( U_MB * 2 )); SQUEEZED_AT=$(date +%s.%N) ;;
        none)   ;;
    esac
    # (53/67/107 % of U are the host's 4000M/5000M/8000M against its ~7.5 G footprint)
    while [ ! -f "$OUT/done" ]; do
        kill -0 "$SAMPLER" 2>/dev/null || break
        sleep 5
    done
fi
wait "$SAMPLER" || true

cat > "$OUT/meta.json" <<EOF
{
  "variant": "$VARIANT",
  "outcome": "$OUTCOME",
  "kernel": "$(uname -r)",
  "ws_mb": $WS_MB, "hot_pct": $HOT_PCT, "cold_pushed_pct": $COLD_PUSHED_PCT, "ratio_x10_for_sizing": $RATIO_X10,
  "memtotal_mb": $MEMTOTAL_MB, "baseline_used_mb": $BASELINE_USED_MB,
  "min_free_kbytes": $MIN_FREE_KB, "watermark_scale_factor": $WSF,
  "zone_low_total_mb": $LOW_TOTAL_MB, "zone_high_total_mb": $HIGH_TOTAL_MB, "free_base_mb": $FREE_BASE_MB,
  "ballast_mb": $BALLAST_MB, "bg": $BG, "bg_keep": $BG_KEEP, "churn_mb": $CHURN_MB, "churn_period_s": $CHURN_PERIOD_S,
  "game_churn_mb": $GAME_CHURN_MB, "game_target_mb": $GAME_TARGET_MB, "settled_usage_u_mb": $U_MB,
  "shrinker_enabled": "$SHRINKER", "highmode": "$HIGHMODE", "squeeze": "$SQUEEZE", "squeezed_at": "${SQUEEZED_AT:-null}",
  "throttle_mibps": "$THROTTLE_MIBPS", "cap_s": $CAP_S, "stall_ms": $STALL_MS,
  "zswap_compressor": "$(cat /sys/module/zswap/parameters/compressor)",
  "zswap_max_pool_percent": $(cat /sys/module/zswap/parameters/max_pool_percent),
  "nr_requests": $(cat /sys/block/vda/queue/nr_requests),
  "mglru": "$(cat /sys/kernel/mm/lru_gen/enabled 2>/dev/null || echo absent)",
  "swappiness": $(sysctl -n vm.swappiness)
}
EOF
echo "run-variant: $VARIANT finished: $(ls "$OUT" | tr '\n' ' ')"
