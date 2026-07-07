#!/usr/bin/env bash
# Apply cgroup-v2 knobs that systemd slice units can't express directly:
#   - ancestor floors      : system.slice + soulmask.slice MemoryMin. cgroup-v2 protection
#                            is HIERARCHICAL — a child's memory.min is capped by every
#                            ancestor's; without these the game/pak floors protect nothing
#                            against global reclaim (plan §1.5 Finding A).
#   - Soulmask container(s): memory.min/low/high protection, zswap writeback (per instance;
#                            decided 2026-07-07: cold tail may page to disk; revert to 0 if
#                            login latency regresses — see MEASUREMENTS.md M4),
#                            io.weight=4950 + io.bfq.weight=1000 (BFQ priority), cpu.weight=800
#   - pak slice            : memory.zswap.max=0 (pak is zstd-incompressible, 1.006× — zswap
#                            wastes CPU+RAM on it; cold pak goes straight to disk)
#   - dev-workloads.slice  : memory.zswap.writeback=1 (cold dev pages may page to disk)
#   - bench containers     : io.weight=1, io.bfq.weight=1, io.max hard IOPS/bandwidth cap
#                            (devcontainer runs docker-repack; test-runner; buildx_buildkit_*)
# Idempotent; tolerant if containers aren't running yet.
#
# N-instance (2026-07-07): iterates EVERY running Soulmask (WSServer) container,
# not just "first found wins". Each container's server UUID (== its docker name,
# Wings' convention) selects its config from /etc/gstammtisch/instance-defaults.env
# + /etc/gstammtisch/instances.d/<uuid>.env (see soulmask-instance-lib.sh and
# SOULMASK.md "Multi-instance operations"). system.slice's MemoryMin is now
# DYNAMIC — sum of every applied instance's SOULMASK_MIN + 1G headroom for host
# daemons — unless SYSTEM_SLICE_MIN is explicitly set in the environment.
#
# Requires BFQ scheduler on vda (/etc/modules-load.d/bfq.conf + udev rule).
# IMPORTANT: never call this during Soulmask startup. Apply only once the server is
# ready. Readiness = the "[SERVER_LIST] registe server ... succeed." line in the game
# log (sic). RCON responding is NOT a readiness/health signal — the RCON thread stays
# hot while the game thread can still be loading or swap-stalled.
# See soulmask-cgroup-watcher.service which enforces this automatically (per instance).
set -uo pipefail
CG="${CG:-/sys/fs/cgroup}"
log(){ echo "[cgroups] $*"; }

# shellcheck source=/usr/local/sbin/soulmask-instance-lib.sh
LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  . "$LIB"
else
  log "FATAL: $LIB not found (expected alongside this script)"; exit 1
fi

_parse_bytes() {
  local v="$1"
  case "$v" in
    *G) echo $(( ${v%G} * 1073741824 )) ;;
    *M) echo $(( ${v%M} * 1048576 )) ;;
    *)  echo "$v" ;;
  esac
}

# I/O cap for bench containers: 30 MB/s, 100 r-IOPS, 400 w-IOPS.
# io.bfq.weight range is 1–1000 (unlike io.weight which is 1–10000).
#
# With the pak ramdisk active (soulmask-pak-ramdisk.service), pak pages are
# tmpfs-backed anon — they go through zswap, not page cache, so bench file I/O
# cannot evict them. The io.max cap here limits benchmark impact on Soulmask's
# periodic DB saves (which DO go through the real disk via writeback threads).
# No memory.high on bench: the devcontainer is also VSCode — capping total
# cgroup memory kills the IDE. BFQ io.weight + io.max is the right lever.
BENCH_RBPS="${BENCH_RBPS:-31457280}"
BENCH_WBPS="${BENCH_WBPS:-31457280}"
# read iops has high impact on even only-cpu bound processes like soulmask.
# a saturated disk cause by other processes will cause lags ingame as a side effect!
# need to test r-iops on system and use maybe 2/3
BENCH_RIOPS_ENV="${BENCH_RIOPS:-}"      # remember if caller set it explicitly
BENCH_RIOPS="${BENCH_RIOPS:-200}"
BENCH_WIOPS="${BENCH_WIOPS:-400}"

# If a measured disk baseline exists (io-baseline.sh, fio randread), derive the bench
# read cap as 2/3 of the measured max — an explicit BENCH_RIOPS env still wins.
IO_BASELINE=/var/lib/gstammtisch/io-baseline.env
if [ -z "$BENCH_RIOPS_ENV" ] && [ -f "$IO_BASELINE" ]; then
  RIOPS_MAX=""
  . "$IO_BASELINE" 2>/dev/null || true
  if [ -n "${RIOPS_MAX:-}" ]; then
    BENCH_RIOPS=$(( RIOPS_MAX * 2 / 3 ))
    log "io-baseline: RIOPS_MAX=${RIOPS_MAX} → BENCH_RIOPS=${BENCH_RIOPS}"
  fi
fi

# --- ancestor protection floors (plan §1.5 Finding A) ---
# system.slice floor = SUM of every applied instance's game floor + ~1G for host
# daemons (sshd/dockerd/wings — also keeps SSH responsive under pressure). Do NOT
# set this below any instance's SOULMASK_MIN: the parent's MemoryMin CAPS the
# child's effective floor (the 5G-floor login-failure regime, silently repeated).
# set-property persists as a systemd drop-in.
# The +1G isn't a safety-margin choice — the parent's MemoryMin caps the child's
# effective floor, so the parent must be >= sum(instance floors) + a little for
# sshd/dockerd/wings (which also fixes "can't SSH in under pressure"). With one
# 6G instance that's 6+1=7G — the original single-instance default, preserved
# below as the explicit-override fallback and as what the dynamic sum computes
# to when only that one instance is running.
SYSTEM_SLICE_MIN_EXPLICIT="${SYSTEM_SLICE_MIN:-}"
# soulmask.slice only parents the pak slice today; must be >= the pak floor.
# Host-wide (the pak tmpfs/slice is shared across instances), not per-instance.
SOULMASK_SLICE_MIN="${SOULMASK_SLICE_MIN:-1G}"

# Discover the block device backing the pterodactyl volume directory.
# MAJ:MIN for raw io.max writes; the device node PATH for systemd IO*Max=
# properties (set-property needs a path, not maj:min).
# Prefers the pterodactyl path; falls back through docker data dir to root.
_io_dev() {
  local dev
  for path in /var/lib/pterodactyl/volumes /var/lib/docker /; do
    dev=$(findmnt -no MAJ:MIN --target "$path" 2>/dev/null) && echo "$dev" && return
  done
}
_io_dev_path() {
  local dev
  for path in /var/lib/pterodactyl/volumes /var/lib/docker /; do
    dev=$(findmnt -no SOURCE --target "$path" 2>/dev/null) && echo "$dev" && return
  done
}
IO_DEV=$(_io_dev)
IO_DEV_PATH=$(_io_dev_path)
[ -n "$IO_DEV" ] && log "discovered io device: $IO_DEV ($IO_DEV_PATH)" || log "WARN: could not discover block device for io.max"

# Pak is zstd-incompressible (1.006× measured) — bypass zswap entirely so cold pak
# pages go straight to disk swap. Also declared in the soulmask-paks.slice unit
# (MemoryZSwapMax=0); asserted here in case the unit predates that setting.
# One shared pak slice for every instance's (opt-in) shared pak tmpfs — not
# per-instance, applied unconditionally regardless of which instances are running.
PAK_CG="$CG/soulmask.slice/soulmask-paks.slice"
if [ -w "$PAK_CG/memory.zswap.max" ]; then
  echo 0 > "$PAK_CG/memory.zswap.max" && log "soulmask-paks.slice memory.zswap.max=0 (pak bypasses zswap)"
fi

# --- dev workloads: allow cold dev pages to page out to disk (2026-07-07) ---
DEV="$CG/dev-workloads.slice"
if [ -d "$DEV" ] && [ -w "$DEV/memory.zswap.writeback" ]; then
  echo 1 > "$DEV/memory.zswap.writeback" && log "dev-workloads.slice memory.zswap.writeback=1"
else
  log "dev-workloads.slice not present yet (start it / launch a dev container first)"
fi

_cg_write() {
  local file="$1" val="$2" label="$3"
  if echo "$val" > "$file" 2>/tmp/cg-write-err; then
    log "  $label = $val"
  else
    log "  WARN: $label write failed: $(cat /tmp/cg-write-err)"
  fi
}

# --- locate every running Soulmask instance and apply its knobs ---
TOTAL_MIN_BYTES=0
INSTANCE_COUNT=0

# Optional per-pass restriction (space-separated container IDs), set by the
# watcher: only the listed instances get their game knobs applied. Empty =
# apply to ALL running instances (manual runs). This is the readiness gate
# for siblings: when instance B registers while instance A is still
# cold-loading, the watcher lists only B (plus previously-applied instances),
# so A does NOT get the production band prematurely (early memory.high can
# crash or deadlock a still-loading server).
APPLY_CIDS="${SOULMASK_APPLY_CIDS:-}"
_cid_allowed() {
  [ -z "$APPLY_CIDS" ] && return 0
  case " $APPLY_CIDS " in *" $1 "*) return 0 ;; esac
  return 1
}

mapfile -t SOULMASK_CIDS < <(soulmask_running_cids)
if [ "${#SOULMASK_CIDS[@]}" -eq 0 ]; then
  log "no Soulmask container running; skipping per-instance cgroup knobs."
fi

for CID in "${SOULMASK_CIDS[@]:-}"; do
  [ -n "$CID" ] || continue
  UUID=$(soulmask_uuid_of "$CID")
  if [ -z "$UUID" ]; then
    log "WARN: could not resolve UUID for container $CID; skipping."
    continue
  fi

  if ! _cid_allowed "$CID"; then
    log "[$UUID] not in SOULMASK_APPLY_CIDS — skipping this pass (not ready yet)"
    continue
  fi

  SCOPE=$(soulmask_cgroup_of "$CID") || {
    log "[$UUID] could not resolve cgroup (cgroup v2 unified?); skipping."
    continue
  }

  soulmask_load_instance_env "$UUID"
  log "[$UUID] role=$ROLE cgroup: $SCOPE"

  # ── Apply via systemctl set-property, NOT raw cgroup writes ──────────────────
  # Docker scopes are transient systemd units. On EVERY `systemctl daemon-reload`
  # (e.g. any apt package that ships units!) systemd re-applies its own recorded
  # properties to the scope's cgroup — silently WIPING raw-written values.
  # Observed 2026-07-07: `apt install systemd-oomd` reset the game scope to
  # min=0/high=max ~1h after the watcher had applied+verified the band.
  # set-property --runtime makes systemd itself the owner of our values, so a
  # reload RE-APPLIES them instead. --runtime: lives as long as the scope does.
  UNIT="${SCOPE##*/}"
  WB_BOOL=$([ "${SOULMASK_WRITEBACK:-1}" = "0" ] && echo no || echo yes)
  if systemctl set-property --runtime "$UNIT" \
       MemoryMin="$SOULMASK_MIN" MemoryLow="$SOULMASK_LOW" MemoryHigh="$SOULMASK_HIGH" \
       MemoryZSwapWriteback="$WB_BOOL" CPUWeight=800 IOWeight=4950 2>/tmp/cg-write-err; then
    log "  [$UUID] set-property $UNIT: min=$SOULMASK_MIN low=$SOULMASK_LOW high=$SOULMASK_HIGH zswap_writeback=$WB_BOOL cpu.weight=800 io.weight=4950"
  else
    # writeback=1 rationale (2026-07-07): the game's genuinely-cold tail (~4G
    # observed) may page to disk; zswap LRU writes back only coldest-of-cold.
    # Revert lever: SOULMASK_WRITEBACK=0 in this instance's env (MEASUREMENTS.md M4 is the gate).
    log "  [$UUID] WARN: set-property failed ($(cat /tmp/cg-write-err)) — raw-write fallback (will NOT survive daemon-reload)"
    _cg_write "$SCOPE/memory.high"            "$SOULMASK_HIGH" "memory.high"
    _cg_write "$SCOPE/memory.low"             "$SOULMASK_LOW"  "memory.low"
    _cg_write "$SCOPE/memory.min"             "$SOULMASK_MIN"  "memory.min"
    _cg_write "$SCOPE/memory.zswap.writeback" "${SOULMASK_WRITEBACK:-1}" "memory.zswap.writeback"
    _cg_write "$SCOPE/io.weight"              "default 4950"   "io.weight"
    _cg_write "$SCOPE/cpu.weight"             "800"            "cpu.weight"
  fi
  # io.bfq.weight has no systemd property — raw write. systemd doesn't manage this
  # attribute, so (unlike the ones above) it survives daemon-reload untouched.
  _cg_write "$SCOPE/io.bfq.weight"          "default 1000"   "io.bfq.weight"
  # Verify what actually landed in the cgroup (detects Wings/systemd overrides)
  log "  [$UUID] verify: min=$(cat "$SCOPE/memory.min" | awk '{printf "%dM",$1/1048576}') high=$(cat "$SCOPE/memory.high" | awk 'BEGIN{m=1073741824} $0=="max"{print "max";exit} {printf "%dG",$1/m}') writeback=$(cat "$SCOPE/memory.zswap.writeback")"

  TOTAL_MIN_BYTES=$(( TOTAL_MIN_BYTES + $(_parse_bytes "$SOULMASK_MIN") ))
  INSTANCE_COUNT=$(( INSTANCE_COUNT + 1 ))
done

# --- system.slice MemoryMin: explicit override, else dynamic sum + 1G ---
if [ -n "$SYSTEM_SLICE_MIN_EXPLICIT" ]; then
  SYSTEM_SLICE_MIN="$SYSTEM_SLICE_MIN_EXPLICIT"
  log "system.slice MemoryMin: explicit override SYSTEM_SLICE_MIN=$SYSTEM_SLICE_MIN"
elif [ "$INSTANCE_COUNT" -gt 0 ]; then
  SYSTEM_SLICE_MIN_BYTES=$(( TOTAL_MIN_BYTES + 1073741824 ))
  if [ $(( SYSTEM_SLICE_MIN_BYTES % 1073741824 )) -eq 0 ]; then
    SYSTEM_SLICE_MIN="$(( SYSTEM_SLICE_MIN_BYTES / 1073741824 ))G"
  else
    SYSTEM_SLICE_MIN="$(( SYSTEM_SLICE_MIN_BYTES / 1048576 ))M"
  fi
  log "system.slice MemoryMin computed: sum(${INSTANCE_COUNT} instance min(s))=$(( TOTAL_MIN_BYTES/1048576 ))M + 1G headroom = $SYSTEM_SLICE_MIN"
else
  SYSTEM_SLICE_MIN="7G"   # fallback when no instance is running yet (matches the historical single-instance default: 6G + 1G)
  log "no running instances — system.slice MemoryMin fallback default: $SYSTEM_SLICE_MIN"
fi

systemctl set-property system.slice MemoryMin="$SYSTEM_SLICE_MIN" 2>/dev/null \
  && log "system.slice MemoryMin=$SYSTEM_SLICE_MIN (protection chain for game floor(s))" \
  || log "WARN: set-property system.slice MemoryMin failed"
systemctl set-property soulmask.slice MemoryMin="$SOULMASK_SLICE_MIN" 2>/dev/null \
  && log "soulmask.slice MemoryMin=$SOULMASK_SLICE_MIN (protection chain for pak floor)" \
  || log "WARN: set-property soulmask.slice MemoryMin failed"

# --- throttle bench containers (devcontainer + test-runner + buildkit) ---
# docker-repack runs inside the devcontainer; test-runner is a separate bench
# container; buildx_buildkit_* runs all buildx bake / cmru builds.
# (writeback stays at the default 1 — cold bench pages may page to disk.)
# We identify by image name patterns rather than fixed IDs since those change on restart.
_apply_bench_limits() {
  local cid="$1" label="$2"
  local pid scope unit
  pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null || true)
  [ -z "$pid" ] && return
  scope="$CG$(awk -F: '/^0::/{print $3}' /proc/$pid/cgroup 2>/dev/null)"
  # buildkitd nests its own sub-cgroups INSIDE the container (PID 1's cgroup
  # path continues below docker-<id>.scope). Trim to the scope component so
  # the systemd unit name is right; limits on the scope cover the subtree.
  case "$scope" in *".scope/"*) scope="${scope%%.scope/*}.scope" ;; esac
  [ -d "$scope" ] || return
  unit="${scope##*/}"
  # set-property so a daemon-reload re-applies instead of wiping (see game section)
  if [ -n "$IO_DEV_PATH" ] && systemctl set-property --runtime "$unit" IOWeight=1 \
       "IOReadBandwidthMax=$IO_DEV_PATH $BENCH_RBPS" "IOWriteBandwidthMax=$IO_DEV_PATH $BENCH_WBPS" \
       "IOReadIOPSMax=$IO_DEV_PATH $BENCH_RIOPS" "IOWriteIOPSMax=$IO_DEV_PATH $BENCH_WIOPS" 2>/dev/null; then
    log "$label ($cid): set-property io.weight=1, io.max=${BENCH_RIOPS}r/${BENCH_WIOPS}w IOPS $((BENCH_RBPS/1048576))MB/s"
  else
    echo "default 1" > "$scope/io.weight"     2>/dev/null
    if [ -n "$IO_DEV" ]; then
      echo "$IO_DEV rbps=${BENCH_RBPS} wbps=${BENCH_WBPS} riops=${BENCH_RIOPS} wiops=${BENCH_WIOPS}" \
        > "$scope/io.max" 2>/dev/null
    fi
    log "$label ($cid): raw-write io.weight=1, io.max=${BENCH_RIOPS}r/${BENCH_WIOPS}w IOPS (will NOT survive daemon-reload)"
  fi
  echo "default 1" > "$scope/io.bfq.weight" 2>/dev/null   # no systemd property; survives reload
}

for c in $(docker ps -q 2>/dev/null); do
  img=$(docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null || true)
  name=$(docker inspect -f '{{.Name}}' "$c" 2>/dev/null | tr -d '/' || true)
  case "$img" in
    *test-runner*) _apply_bench_limits "$c" "test-runner" ;;
  esac
  case "$name" in
    *devcontainer*|*dstdns-devcontainer*) _apply_bench_limits "$c" "devcontainer" ;;
    buildx_buildkit_*)                    _apply_bench_limits "$c" "buildkit" ;;
  esac
done
