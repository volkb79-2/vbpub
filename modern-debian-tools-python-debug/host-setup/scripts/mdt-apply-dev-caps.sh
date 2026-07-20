#!/usr/bin/env bash
# mdt host-setup — runtime half of the dev-tier governance. Everything the
# static slice units can't express:
#   - besteffort.slice IO*Max at BE_IO_CAP_PCT% of the MEASURED device
#     ceilings (io-baseline.env) — replaces the deliberately tight unit-file
#     statics for the whole tier
#   - interactive.slice memory.zswap.writeback (raw-write fallback for
#     systemd < 256 where the MemoryZSwapWriteback= directive doesn't exist;
#     harmless double-set on newer hosts)
#   - per-container caps: test-runner/buildx_buildkit_*/devcontainer scopes get
#     io.max at BENCH_IO_CAP_PCT% of the baseline (bench+buildkit additionally
#     get IOWeight=1; the devcontainer does not — it is the IDE). Docker scopes
#     are transient units: they exist only while the container runs, so this
#     can only ever be done at runtime, never declaratively in a unit file.
#   - cgroup2 mount-flag check: without memory_recursiveprot every slice-level
#     MemoryLow/MemoryMin silently stops protecting container pages
# Idempotent; tolerant of missing docker/baseline/slices. Config:
# /etc/mdt/host-setup.env (see host-setup.env.example). Run by
# mdt-host-slices.service at boot + mdt-host-slices.timer periodically.
set -uo pipefail
CG="${CG:-/sys/fs/cgroup}"
CONF="${CONF:-/etc/mdt/host-setup.env}"
log(){ echo "[mdt-dev-caps] $*"; }

# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF" || log "WARN: $CONF not found — using built-in defaults"

BE_IO_CAP_PCT="${BE_IO_CAP_PCT:-40}"
BENCH_IO_CAP_PCT="${BENCH_IO_CAP_PCT:-80}"
BENCH_IMAGE_PATTERNS="${BENCH_IMAGE_PATTERNS:-*test-runner*}"
BENCH_NAME_PATTERNS="${BENCH_NAME_PATTERNS:-buildx_buildkit_*}"
DEVCONTAINER_NAME_PATTERNS="${DEVCONTAINER_NAME_PATTERNS:-*devcontainer*}"
CGROUP2_FLAGS="${CGROUP2_FLAGS:-warn}"
IO_BASELINE_ENV="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"
INTERACTIVE_ZSWAP_WRITEBACK="${INTERACTIVE_ZSWAP_WRITEBACK:-no}"

# --- cgroup2 mount flags -----------------------------------------------------
# systemd mounts cgroup2 with nsdelegate,memory_recursiveprot at boot; a later
# remount from the init cgroup namespace can strip them. Only processes in the
# init cgroup namespace can change them back — i.e. this script on the host,
# NOT anything running inside a container.
CG_OPTS=$(findmnt -no OPTIONS "$CG" 2>/dev/null || true)
if [ -n "$CG_OPTS" ] && ! echo "$CG_OPTS" | grep -q memory_recursiveprot; then
  if [ "$CGROUP2_FLAGS" = "fix" ]; then
    if mount -o remount,nsdelegate,memory_recursiveprot "$CG" 2>/dev/null; then
      log "cgroup2: restored nsdelegate,memory_recursiveprot (was: $CG_OPTS)"
    else
      log "WARN: cgroup2 remount failed — MemoryLow/MemoryMin will not protect container pages"
    fi
  else
    log "WARN: cgroup2 mounted WITHOUT memory_recursiveprot — slice MemoryLow/MemoryMin do NOT reach container pages. Fix: mount -o remount,nsdelegate,memory_recursiveprot $CG (or set CGROUP2_FLAGS=fix)"
  fi
fi

# --- device discovery ---------------------------------------------------------
# Node PATH for systemd IO*Max= set-property (needs a path, not MAJ:MIN).
if [ -z "${IO_DEV_PATH:-}" ]; then
  for path in /var/lib/docker /; do
    IO_DEV_PATH=$(findmnt -no SOURCE --target "$path" 2>/dev/null) && [ -n "$IO_DEV_PATH" ] && break
  done
fi
# Strip partition/mapper indirection is deliberately NOT attempted: caps on the
# partition node work; if you want the whole-disk node, set IO_DEV_PATH.
[ -n "${IO_DEV_PATH:-}" ] && log "io device: $IO_DEV_PATH" \
  || log "WARN: no block device discovered — all IO cap steps will be skipped"

# --- besteffort.slice: tier-wide measured IO caps -----------------------------
if [ -f "$IO_BASELINE_ENV" ]; then
  RIOPS_MAX="" WIOPS_MAX="" RBW_MAX_BPS="" WBW_MAX_BPS="" MEASURE_METHOD=""
  # shellcheck disable=SC1090
  . "$IO_BASELINE_ENV" 2>/dev/null || true
  # Provenance: the numbers alone can't say how they were measured, and the
  # methods differ in a direction we can't see. sustained-v3 is ours; burst-v1
  # is `ciu iops-baseline` (unramped 1G/10s — reads HIGH on a VM, so caps
  # derived from it are looser than the percentage suggests).
  case "${MEASURE_METHOD:-}" in
    sustained-v3) : ;;
    "")           log "WARN: $IO_BASELINE_ENV has no MEASURE_METHOD — provenance unknown; caps may not be the intended fraction of sustained capacity" ;;
    burst-v1)     log "WARN: baseline method=burst-v1 (ciu iops-baseline, unramped 1G/10s) — reads high on a VM; run mdt-io-baseline.py for a sustained measurement" ;;
    *)            log "WARN: baseline method=$MEASURE_METHOD is UNRECOGNISED — treat the derived caps as unverified" ;;
  esac
  if [ -n "${RIOPS_MAX:-}" ] && [ -n "${WIOPS_MAX:-}" ] && [ -n "${RBW_MAX_BPS:-}" ] \
     && [ -n "${WBW_MAX_BPS:-}" ] && [ -n "${IO_DEV_PATH:-}" ]; then
    BE_RIOPS=$(( RIOPS_MAX * BE_IO_CAP_PCT / 100 ))
    BE_WIOPS=$(( WIOPS_MAX * BE_IO_CAP_PCT / 100 ))
    BE_RBPS=$(( RBW_MAX_BPS * BE_IO_CAP_PCT / 100 ))
    BE_WBPS=$(( WBW_MAX_BPS * BE_IO_CAP_PCT / 100 ))
    # 0 in io.max is not "unlimited" — it halts IO. Refuse a bad baseline and
    # leave the unit-file statics (tight, but a working host) in force.
    if [ "$BE_RIOPS" -lt 1 ] || [ "$BE_WIOPS" -lt 1 ] || [ "$BE_RBPS" -lt 1 ] || [ "$BE_WBPS" -lt 1 ]; then
      log "WARN: baseline yields a <= 0 tier cap — besteffort.slice keeps unit-file statics"
    # --runtime: survives daemon-reload (runtime drop-in), gone at reboot —
    # which is exactly right, this service re-runs at every boot.
    elif systemctl set-property --runtime besteffort.slice \
         "IOReadBandwidthMax=$IO_DEV_PATH $BE_RBPS" "IOWriteBandwidthMax=$IO_DEV_PATH $BE_WBPS" \
         "IOReadIOPSMax=$IO_DEV_PATH $BE_RIOPS" "IOWriteIOPSMax=$IO_DEV_PATH $BE_WIOPS" 2>/tmp/mdt-cg-err; then
      log "besteffort.slice: io.max=${BE_RIOPS}r/${BE_WIOPS}w IOPS $((BE_RBPS/1048576))/$((BE_WBPS/1048576))MB/s r/w (${BE_IO_CAP_PCT}% of baseline)"
    else
      log "WARN: besteffort.slice set-property failed ($(cat /tmp/mdt-cg-err 2>/dev/null)) — unit-file statics remain in force"
    fi
  else
    log "baseline file incomplete or no device — besteffort.slice keeps unit-file statics"
  fi
else
  log "no $IO_BASELINE_ENV — besteffort.slice keeps unit-file statics (run mdt-io-baseline.py)"
fi

# --- interactive.slice: zswap writeback policy --------------------------------
# Raw cgroupfs write: fallback for systemd < 256 (no MemoryZSwapWriteback=
# directive) and for a slice activated before the unit carried the directive.
case "$INTERACTIVE_ZSWAP_WRITEBACK" in
  no|0|false) ZSWAP_WB=0 ;;
  *)          ZSWAP_WB=1 ;;
esac
IA="$CG/interactive.slice"
if [ -d "$IA" ] && [ -w "$IA/memory.zswap.writeback" ]; then
  echo "$ZSWAP_WB" > "$IA/memory.zswap.writeback" \
    && log "interactive.slice memory.zswap.writeback=$ZSWAP_WB"
else
  log "interactive.slice not active yet (starts with the first devcontainer) — skipped zswap policy"
fi

# --- per-container caps: bench / buildkit / devcontainer ----------------------
# These target the transient docker-<id>.scope of each matched container —
# scopes exist only while the container runs, so this is runtime-only by
# nature. The periodic timer catches containers created between runs.
_match() { # _match "value" "pattern1 pattern2 ..."
  local v="$1" p
  # shellcheck disable=SC2254  # unquoted on purpose: patterns SHOULD glob
  for p in $2; do case "$v" in $p) return 0 ;; esac; done
  return 1
}

# _apply_container_caps <container-id> <label> [deprioritize]
# deprioritize=1 (bench/buildkit): also drop the scope to the lowest IO weight,
# so it yields the device to everything else. NOT used for the devcontainer —
# that scope lives in interactive.slice and IS the IDE; capping its peak rate is
# the goal, making it lose every IO race to a sibling is not.
_apply_container_caps() {
  local cid="$1" label="$2" deprio="${3:-0}" pid scope unit props=()
  pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null) || return 0
  { [ -z "$pid" ] || [ "$pid" = "0" ]; } && return 0
  [ -n "${IO_DEV_PATH:-}" ] || { log "WARN: $label ($cid): no io device — skipped"; return 0; }
  scope="$CG$(awk -F: '/^0::/{print $3}' "/proc/$pid/cgroup" 2>/dev/null)"
  # buildkitd nests sub-cgroups INSIDE its container; trim to the scope
  # component — limits on the scope cover the whole subtree.
  case "$scope" in *".scope/"*) scope="${scope%%.scope/*}.scope" ;; esac
  [ -d "$scope" ] || return 0
  unit="${scope##*/}"
  [ "$deprio" = 1 ] && props+=(IOWeight=1)
  props+=("IOReadBandwidthMax=$IO_DEV_PATH $BENCH_RBPS" "IOWriteBandwidthMax=$IO_DEV_PATH $BENCH_WBPS"
          "IOReadIOPSMax=$IO_DEV_PATH $BENCH_RIOPS"     "IOWriteIOPSMax=$IO_DEV_PATH $BENCH_WIOPS")
  # set-property (not a raw io.max write): systemd re-applies its own recorded
  # properties to a scope on every daemon-reload, silently wiping raw writes.
  if systemctl set-property --runtime "$unit" "${props[@]}" 2>/dev/null; then
    log "$label ($cid): io.max=${BENCH_RIOPS}r/${BENCH_WIOPS}w IOPS $((BENCH_RBPS/1048576))/$((BENCH_WBPS/1048576))MB/s r/w (${BENCH_SRC})$([ "$deprio" = 1 ] && echo ', io.weight=1')"
  else
    log "WARN: $label ($cid): set-property failed — skipped"
    return 0
  fi
  # BFQ per-scope weight has no systemd property — raw write, and because
  # systemd does not manage this attribute the write survives daemon-reload.
  [ "$deprio" = 1 ] && { echo "default 1" > "$scope/io.bfq.weight" 2>/dev/null || true; }
  return 0
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  # Per-container ceilings: BENCH_IO_CAP_PCT% of baseline, static fallbacks
  # when no baseline exists (tight on purpose — measure!).
  BENCH_RBPS="${BENCH_RBPS:-31457280}"; BENCH_WBPS="${BENCH_WBPS:-31457280}"
  BENCH_RIOPS="${BENCH_RIOPS:-200}";    BENCH_WIOPS="${BENCH_WIOPS:-400}"
  BENCH_SRC="static fallback — no baseline, run mdt-io-baseline.py"
  # All four or none: a partial baseline would derive a 0 cap, and 0 in io.max
  # is not "unlimited", it stops the container's IO dead.
  if [ -n "${RIOPS_MAX:-}" ] && [ -n "${WIOPS_MAX:-}" ] \
     && [ -n "${RBW_MAX_BPS:-}" ] && [ -n "${WBW_MAX_BPS:-}" ]; then
    BENCH_RIOPS=$(( RIOPS_MAX   * BENCH_IO_CAP_PCT / 100 ))
    BENCH_WIOPS=$(( WIOPS_MAX   * BENCH_IO_CAP_PCT / 100 ))
    BENCH_RBPS=$((  RBW_MAX_BPS * BENCH_IO_CAP_PCT / 100 ))
    BENCH_WBPS=$((  WBW_MAX_BPS * BENCH_IO_CAP_PCT / 100 ))
    BENCH_SRC="${BENCH_IO_CAP_PCT}% of baseline"
  fi
  if [ "$BENCH_RIOPS" -lt 1 ] || [ "$BENCH_WIOPS" -lt 1 ] \
     || [ "$BENCH_RBPS" -lt 1 ] || [ "$BENCH_WBPS" -lt 1 ]; then
    log "WARN: derived per-container cap <= 0 (bad baseline?) — skipping the container sweep"
    BENCH_SWEEP=0
  fi
  for c in $([ "${BENCH_SWEEP:-1}" = 1 ] && docker ps -q 2>/dev/null); do
    img=$(docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null || true)
    name=$(docker inspect -f '{{.Name}}' "$c" 2>/dev/null | tr -d '/' || true)
    if _match "$img" "$BENCH_IMAGE_PATTERNS"; then
      _apply_container_caps "$c" "bench:$name" 1
    elif _match "$name" "$BENCH_NAME_PATTERNS"; then
      _apply_container_caps "$c" "buildkit:$name" 1
    elif _match "$name" "$DEVCONTAINER_NAME_PATTERNS"; then
      _apply_container_caps "$c" "devcontainer:$name" 0
    fi
  done
else
  log "docker unavailable — skipped per-container sweep"
fi

log "done"
