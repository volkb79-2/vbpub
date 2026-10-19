#!/usr/bin/env bash
# mdt host-setup — runtime half of the dev-tier governance. Everything the
# static slice units can't express:
#   - dev.slice (the shared root) IO*Max: operator-pinned DEV_IO_*_MAX keys,
#     else DEV_IO_CAP_PCT% of a current PLAUSIBLE baseline (io-baseline.env),
#     else the static fallback; min() when both a pin and a baseline exist.
#     Always read back from the kernel's io.max and verified (non-zero exit on
#     mismatch). One absolute ceiling on the shared parent for EVERY child
#     combined (dev-interactive.slice, dev-background.slice, dev-gates.slice,
#     ...), not one per child.
#   - every dev slice's memory.zswap.writeback (raw-write fallback for systemd
#     < 256 where the MemoryZSwapWriteback= directive doesn't exist; harmless
#     double-set on newer hosts)
#   - per-container caps: every Docker scope under a governed child gets
#     io.max at WATCHER_IO_CAP_PCT% of the benchmark results (gates and the
#     managed BuildKit scope additionally get IOWeight=1; interactive and
#     background scopes do not). An out-of-tree Buildx worker is selected only
#     by WATCHER_BUILDKIT_NAME_PATTERNS. Docker scopes are transient units:
#     they exist only while the container runs, so this can only ever be done
#     at runtime, never declaratively in a unit file. This sweep is the BACKSTOP:
#     mdt-container-io-events-watcher.service applies the same caps within the same
#     second of container start via `docker events` — see
#     mdt-container-io-caps.lib.sh (shared by both) and host-setup/README.md
#     "Persistence model".
#   - cgroup2 mount-flag check: without memory_recursiveprot every slice-level
#     MemoryLow/MemoryMin silently stops protecting container pages
# Idempotent; tolerant of missing docker/baseline/slices. Config:
# /etc/mdt/host-setup.env (see host-setup.env.example). Run by
# mdt-dev-governance-reconcile.service at boot + mdt-dev-governance-reconcile.timer periodically.
set -uo pipefail
CG="${CG:-/sys/fs/cgroup}"
CONF="${CONF:-/etc/mdt/host-setup.env}"
log(){ echo "[mdt-dev-governance-reconcile] $*"; }

# shellcheck source=./mdt-container-io-caps.lib.sh
. "$(dirname "$0")/mdt-container-io-caps.lib.sh"

# _mdt_load_config: sources $CONF, sets WATCHER_IO_CAP_PCT and the optional
# out-of-tree Buildx name patterns.
# IO_BASELINE_ENV — shared with mdt-container-io-events-watcher.sh, see the lib file.
_mdt_load_config
DEV_IO_CAP_PCT="${DEV_IO_CAP_PCT:-60}"
CGROUP2_FLAGS="${CGROUP2_FLAGS:-fix}"
DEV_ZSWAP_WRITEBACK="${DEV_ZSWAP_WRITEBACK:-no}"
DEV_INTERACTIVE_ZSWAP_WRITEBACK="${DEV_INTERACTIVE_ZSWAP_WRITEBACK:-no}"
DEV_BACKGROUND_ZSWAP_WRITEBACK="${DEV_BACKGROUND_ZSWAP_WRITEBACK:-no}"
DEV_GATES_ZSWAP_WRITEBACK="${DEV_GATES_ZSWAP_WRITEBACK:-no}"
DEV_BUILDKITD_ZSWAP_WRITEBACK="${DEV_BUILDKITD_ZSWAP_WRITEBACK:-no}"

# --- cgroup2 mount flags -----------------------------------------------------
# systemd mounts cgroup2 with nsdelegate,memory_recursiveprot at boot; a later
# remount from the init cgroup namespace can strip them (observed live,
# 2026-08-28: present at one sweep, gone five minutes later, with no code in
# this repo doing it — most likely an incidental side effect of an external
# --privileged/--cgroupns=host container touching the host mount namespace).
# Only processes in the init cgroup namespace can change them back — i.e.
# this script on the host, NOT anything running inside a container. Default
# is now CGROUP2_FLAGS=fix: self-heal every sweep (<= WATCHER_INTERVAL of
# exposure) rather than only warn, since this flag being missing silently
# defeats MemoryLow/MemoryMin with no other symptom.
#
# memory_hugetlb_accounting (kernel >= 6.6, systemd >= 260 mounts it by
# default) folds HugeTLB pages into memory.current/memory.max — without it a
# cgroup's hugetlb usage is invisible to the memory controller entirely.
# Kernel-gated here the same way MemoryZSwapWriteback is systemd-gated in
# install.sh: an unsupported mount option fails the WHOLE remount, so it must
# not be offered to a kernel that predates it.
KVER=$(uname -r | grep -oE '^[0-9]+\.[0-9]+')
KMAJOR=${KVER%%.*}
KMINOR=${KVER##*.}
HUGETLB_OPT=""
if [ -n "$KMAJOR" ] && [ -n "$KMINOR" ] \
   && { [ "$KMAJOR" -gt 6 ] || { [ "$KMAJOR" -eq 6 ] && [ "$KMINOR" -ge 6 ]; }; }; then
  HUGETLB_OPT=",memory_hugetlb_accounting"
fi
CG_OPTS=$(findmnt -no OPTIONS "$CG" 2>/dev/null || true)
MISSING=""
if [ -n "$CG_OPTS" ]; then
  echo "$CG_OPTS" | grep -q memory_recursiveprot || MISSING="${MISSING}memory_recursiveprot "
  [ -n "$HUGETLB_OPT" ] && { echo "$CG_OPTS" | grep -q memory_hugetlb_accounting || MISSING="${MISSING}memory_hugetlb_accounting "; }
fi
if [ -n "$MISSING" ]; then
  log "WARN: cgroup2 mounted WITHOUT: ${MISSING}(current opts: $CG_OPTS) — slice MemoryLow/MemoryMin do NOT protect container pages without memory_recursiveprot$([ -n "$HUGETLB_OPT" ] && echo "; hugetlb usage is invisible to memory accounting without memory_hugetlb_accounting")"
  if [ "$CGROUP2_FLAGS" = "fix" ]; then
    if mount -o "remount,nsdelegate,memory_recursiveprot${HUGETLB_OPT}" "$CG" 2>/dev/null; then
      log "cgroup2: restored nsdelegate,memory_recursiveprot${HUGETLB_OPT}"
    else
      log "WARN: cgroup2 remount failed — missing flag(s) remain missing"
    fi
  else
    log "cgroup2: CGROUP2_FLAGS=warn — not auto-fixing. Fix: mount -o remount,nsdelegate,memory_recursiveprot${HUGETLB_OPT} $CG"
  fi
fi

# --- device discovery ---------------------------------------------------------
# Node PATH for systemd IO*Max= set-property (needs a path, not MAJ:MIN).
_mdt_discover_io_dev_path

# --- baseline: shared by the dev.slice cap below AND the per-container caps --
_mdt_load_baseline

# --- dev.slice IO governance -------------------------------------------------
# One apply path for every state (measured baseline, operator-pinned absolute
# keys, static fallback): resolve the intended four numbers, ASSERT them with
# `set-property --runtime`, then READ THE KERNEL'S io.max BACK and compare.
#
# Why there is no "clear" path any more (measured on the live host
# 2026-10-19, MDT-016): `systemctl set-property --runtime dev.slice
# IOReadBandwidthMax=` does NOT "reveal the values declared by the installed
# slice unit".  In systemd an empty assignment resets the WHOLE list, unit-file
# values included, and the runtime drop-in it leaves behind is empty: dev.slice
# ended with an empty io.max — no IO cap at all — while this script logged
# that the static fallback was authoritative.  The runtime layer therefore
# never withdraws a cap: when there is no usable baseline it holds the
# explicit fallback values, re-asserted with `set-property --runtime`
# (D-HS-01-1).  The alternative — removing MDT's runtime drop-ins and running
# `systemctl daemon-reload` — was MEASURED on the live host on 2026-10-19 and
# does not work: after removing a runtime drop-in and a daemon-reload the
# unit-file value was NOT re-applied to the cgroup (memory.swap.max stayed at
# the old runtime value until an explicit set-property).  Deleting systemd's
# private /run/systemd/system.control files also depends on its file naming
# and races its own writes.
#
# The one exception is a host with NO block-device node: the installer drops
# the static IO lines there, the slice is not meant to be capped on a device
# we cannot name, and an empty assignment only removes stale MDT runtime
# values.
MDT_RC=0
_mdt_clear_runtime_io_caps_no_device() {
  local _clear_error _unit
  if _clear_error=$(systemctl set-property --runtime dev.slice \
       IOReadBandwidthMax= IOWriteBandwidthMax= IOReadIOPSMax= IOWriteIOPSMax= 2>&1); then
    log "dev.slice: no host block device was discovered — removed MDT runtime IO values; nothing is capped on a device we cannot name"
  else
    log "WARN: dev.slice runtime IO-value removal failed ($_clear_error)"
  fi
  for _unit in dev-gates.slice dev-buildkitd.slice; do
    if _clear_error=$(systemctl set-property --runtime "$_unit" IOReadIOPSMax= IOWriteIOPSMax= 2>&1); then
      log "$_unit: no host block device was discovered — removed MDT runtime IOPS sub-ceiling"
    else
      log "WARN: $_unit runtime IOPS removal failed ($_clear_error)"
    fi
  done
}

_mdt_apply_dev_io() {
  local majmin err _rc
  _mdt_resolve_dev_io_caps || MDT_RC=1
  if ! majmin=$(_mdt_dev_majmin "$IO_DEV_PATH"); then
    log "ERROR: cannot resolve $IO_DEV_PATH to the whole disk systemd caps (missing node, no sysfs entry, or several backing devices) — IO governance cannot be verified"
    MDT_RC=1
    return
  fi
  if ! err=$(systemctl set-property --runtime dev.slice \
       "IOReadBandwidthMax=$IO_DEV_PATH $DEV_RBPS" "IOWriteBandwidthMax=$IO_DEV_PATH $DEV_WBPS" \
       "IOReadIOPSMax=$IO_DEV_PATH $DEV_RIOPS" "IOWriteIOPSMax=$IO_DEV_PATH $DEV_WIOPS" 2>&1); then
    log "ERROR: dev.slice set-property failed ($err)"
    MDT_RC=1
    return
  fi
  _mdt_verify_io_max dev.slice "$CG/dev.slice" "$majmin" "$DEV_RBPS" "$DEV_WBPS" "$DEV_RIOPS" "$DEV_WIOPS"; _rc=$?
  [ "$_rc" = 1 ] && MDT_RC=1
  if [ "$_rc" = 0 ]; then
    # "authoritative" is only ever claimed after the kernel agreed
    log "dev.slice: io.max on $IO_DEV_PATH (disk $majmin) is authoritative: ${DEV_RIOPS}r/${DEV_WIOPS}w IOPS, ${DEV_RBPS}/${DEV_WBPS} B/s r/w [$DEV_IO_SRC; DEV_IO_CAP_PCT=${DEV_IO_CAP_PCT}] — covers every dev.slice child combined"
  fi
  _mdt_apply_tier_io "$majmin"
}

# Per-tier caps.  dev-gates.slice/dev-buildkitd.slice always get an IOPS
# sub-ceiling: DEV_SUBSLICE_IOPS_PCT% of the EFFECTIVE dev.slice IOPS (measured,
# pinned or static), further reduced to min(sub-ceiling, absolute) by
# DEV_<TIER>_IO_{RIOPS,WIOPS}_MAX.  Every tier (interactive and background
# too) also takes absolute DEV_<TIER>_IO_{RBPS,WBPS,RIOPS,WIOPS}_MAX caps,
# applied only for the values that are set.  Bandwidth otherwise inherits the
# parent's.  These slices carry no static IO line in their unit file, so an
# empty assignment for a value that is NOT set only removes our own earlier
# runtime value (a removed pin must not linger until reboot); it is never
# written to dev.slice, which is the slice meant to stay capped.
_mdt_apply_tier_io() {
  local majmin="$1" pct="${DEV_SUBSLICE_IOPS_PCT:-60}" entry tier unit sub rb wb ri wi sub_ri sub_wi err _rc props
  for entry in $MDT_IO_TIERS; do
    tier=${entry%%:*}; unit=${entry#*:}
    sub=0; case "$unit" in dev-gates.slice|dev-buildkitd.slice) sub=1 ;; esac
    rb=$(_mdt_tier_pin "$tier" RBPS) || MDT_RC=1
    wb=$(_mdt_tier_pin "$tier" WBPS) || MDT_RC=1
    ri=$(_mdt_tier_pin "$tier" RIOPS) || MDT_RC=1
    wi=$(_mdt_tier_pin "$tier" WIOPS) || MDT_RC=1
    if [ "$sub" = 1 ]; then
      sub_ri=$(( DEV_RIOPS * pct / 100 )); sub_wi=$(( DEV_WIOPS * pct / 100 ))
      if [ "$sub_ri" -lt 1 ] || [ "$sub_wi" -lt 1 ]; then
        log "WARN: derived sub-slice IOPS cap <= 0 — $unit keeps only dev.slice's shared ceiling"
        sub_ri=""; sub_wi=""
      fi
      if [ -n "$sub_ri" ]; then
        { [ -z "$ri" ] || [ "$sub_ri" -lt "$ri" ]; } && ri=$sub_ri
        { [ -z "$wi" ] || [ "$sub_wi" -lt "$wi" ]; } && wi=$sub_wi
      fi
    fi
    # nothing configured for a tier that is not a sub-ceiling tier: leave it alone
    [ -n "$rb$wb$ri$wi" ] || continue
    props=()
    if [ -n "$rb" ]; then props+=("IOReadBandwidthMax=$IO_DEV_PATH $rb"); else props+=("IOReadBandwidthMax="); fi
    if [ -n "$wb" ]; then props+=("IOWriteBandwidthMax=$IO_DEV_PATH $wb"); else props+=("IOWriteBandwidthMax="); fi
    if [ -n "$ri" ]; then props+=("IOReadIOPSMax=$IO_DEV_PATH $ri"); else props+=("IOReadIOPSMax="); fi
    if [ -n "$wi" ]; then props+=("IOWriteIOPSMax=$IO_DEV_PATH $wi"); else props+=("IOWriteIOPSMax="); fi
    if err=$(systemctl set-property --runtime "$unit" "${props[@]}" 2>&1); then
      _mdt_verify_io_max "$unit" "$CG/dev.slice/$unit" "$majmin" "$rb" "$wb" "$ri" "$wi"; _rc=$?
      [ "$_rc" = 1 ] && MDT_RC=1
    else
      log "ERROR: $unit IO cap set-property failed ($err)"
      MDT_RC=1
    fi
  done
}

if [ -n "${IO_DEV_PATH:-}" ]; then
  [ "${MDT_IO_BASELINE_VALID:-0}" = 1 ] \
    || log "no current plausible baseline — dev.slice uses its pinned/static values until mdt-io-baseline.py succeeds"
  _mdt_apply_dev_io
else
  _mdt_clear_runtime_io_caps_no_device
fi

# --- swap coverage (D6) ----------------------------------------------------------
# Swap-out IO is charged to the page's memcg, so a dev.slice io.max on the
# disk holding the swap bounds it; a swap disk with no entry is uncapped.
if [ -r "${MDT_PROC_ROOT:-/proc}/swaps" ]; then
  while read -r _swap_dev _swap_disk; do
    [ -n "$_swap_dev" ] || continue
    if [ "$_swap_disk" = unresolved ]; then
      log "WARN: swap device $_swap_dev: its whole disk could not be resolved — cannot confirm swap IO is capped"
    else
      log "WARN: UNCAPPED SWAP: $_swap_dev lies on disk $_swap_disk, which has NO dev.slice io.max entry — a dev container that swaps can saturate that disk"
    fi
  done < <(_mdt_uncapped_swap_devices)
fi

# --- slice zswap writeback policies -------------------------------------------
# Raw cgroupfs write: fallback for systemd < 256 (no MemoryZSwapWriteback=
# directive) and for slices activated before their unit carried the directive.
# Slice names with a dash are nested below dev.slice in cgroupfs, even though
# systemd accepts the unit name directly for set-property.
for _zswap_entry in \
  "dev.slice:DEV_ZSWAP_WRITEBACK" \
  "dev-interactive.slice:DEV_INTERACTIVE_ZSWAP_WRITEBACK" \
  "dev-background.slice:DEV_BACKGROUND_ZSWAP_WRITEBACK" \
  "dev-gates.slice:DEV_GATES_ZSWAP_WRITEBACK" \
  "dev-buildkitd.slice:DEV_BUILDKITD_ZSWAP_WRITEBACK"; do
  IFS=: read -r _zswap_slice _zswap_var <<< "$_zswap_entry"
  _zswap_value="${!_zswap_var:-no}"
  case "$_zswap_value" in
    no|0|false) _zswap_wb=0 ;;
    *)           _zswap_wb=1 ;;
  esac
  if [ "$_zswap_slice" = dev.slice ]; then
    _zswap_path="$CG/dev.slice"
  else
    _zswap_path="$CG/dev.slice/$_zswap_slice"
  fi
  if [ -d "$_zswap_path" ] && [ -w "$_zswap_path/memory.zswap.writeback" ]; then
    echo "$_zswap_wb" > "$_zswap_path/memory.zswap.writeback" \
      && log "$_zswap_slice memory.zswap.writeback=$_zswap_wb"
  else
    log "$_zswap_slice not active yet — skipped zswap policy"
  fi
done

# --- per-container caps: governed child scopes + out-of-tree Buildx ----------
# These target the transient docker-<id>.scope of each governed container —
# scopes exist only while the container runs. mdt-container-io-events-watcher.service now
# applies this instantly on container start via `docker events`; this sweep
# is the backstop for whatever it missed (a restart window, a docker daemon
# restart mid-stream) — see host-setup/README.md "Persistence model". Uses
# the SAME _mdt_match/_mdt_apply_container_caps/_mdt_classify_and_apply as
# the watcher (mdt-container-io-caps.lib.sh), so the two can never drift apart.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  # Per-container ceilings: WATCHER_IO_CAP_PCT% of the current baseline.
  # Without a valid baseline _mdt_derive_watcher_caps sets WATCHER_SKIP=1, so
  # this sweep clears old MDT-owned transient IO properties rather than
  # inventing a host-independent cap that could stall an interactive
  # container. With a valid baseline it applies one percentage to every
  # governed child scopes and explicitly-patterned out-of-tree Buildx workers.
  _mdt_derive_watcher_caps
  if [ "${WATCHER_SKIP:-0}" = 1 ]; then
    _mdt_clear_container_caps
  else
    for c in $(docker ps -q 2>/dev/null); do
      _mdt_classify_and_apply "$c"
    done
  fi
else
  log "docker unavailable — skipped per-container sweep"
fi

log "done"
if [ "$MDT_RC" != 0 ]; then
  log "ERROR: IO governance is NOT verified — exiting non-zero so the failure is visible in systemd"
  exit 1
fi
