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
# IOReadBandwidthMax=` does NOT fall back to the values declared in the
# installed slice unit (the old comment here claimed it did).  In systemd an empty assignment resets the WHOLE list, unit-file
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
# No block device discovered at run time.  This reconcile runs every minute and
# findmnt can fail transiently (or at boot ordering), so an absent device is NEVER
# a reason to write: an empty assignment would wipe the unit-file cap too.  If
# dev.slice carries IO limits, refuse loudly (non-zero exit); if it carries none
# there is nothing to cap and nothing to clear.
_mdt_no_device() {
  local shown raw
  # "could not read" is indeterminate, NOT "no limits": an empty string from a failed
  # systemctl would otherwise certify "nothing to cap" (absence for emptiness)
  if ! raw=$(systemctl show dev.slice -p IOReadBandwidthMax -p IOWriteBandwidthMax \
               -p IOReadIOPSMax -p IOWriteIOPSMax 2>&1); then
    log "ERROR: could not read dev.slice's IO limits (systemctl show failed: $(printf '%s' "$raw" | tr '\n' ' ')) while no block device was discovered — cannot tell whether dev.slice is capped; this run fails"
    MDT_RC=1
    return
  fi
  shown=$(printf '%s\n' "$raw" | grep -E '=.+' || true)
  if [ -n "$shown" ]; then
    log "ERROR: no block device was discovered but dev.slice carries IO limits ($(printf '%s' "$shown" | tr '\n' ' ')) — refusing to wipe them; the current values stay in force and this run fails until the device is discoverable (set IO_DEV_PATH in host-setup.env)"
    MDT_RC=1
  else
    log "no host block device was discovered and dev.slice carries no IO limit — nothing to cap and nothing to clear"
  fi
}

_mdt_apply_dev_io() {
  local majmin _rc sd_dev
  _mdt_resolve_dev_io_caps || MDT_RC=1
  if ! majmin=$(_mdt_dev_majmin "$IO_DEV_PATH" 2>&1); then
    log "ERROR: cannot resolve $IO_DEV_PATH to the whole disk systemd caps: ${majmin:-missing node or no sysfs entry} — IO governance cannot be verified"
    MDT_RC=1
    return
  fi
  if ! sd_dev=$(_mdt_io_devname_for_majmin "$majmin" 2>&1); then
    log "ERROR: cannot name the whole disk $majmin for systemd: $sd_dev — IO governance cannot be applied"
    MDT_RC=1
    return
  fi
  MDT_IO_PROPS=()
  _mdt_io_props "$sd_dev" "$DEV_RBPS" "$DEV_WBPS" "$DEV_RIOPS" "$DEV_WIOPS"
  # The whole batch (dev.slice and every capped tier) is applied first and verified TOGETHER; a stale
  # per-spelling entry is healed by ONE daemon-reload for all of them (MDT-HS-06), never unit by unit.
  _mdt_io_queue_reset
  _mdt_io_queue_add dev.slice "$CG/dev.slice" "$majmin" "$DEV_RBPS" "$DEV_WBPS" "$DEV_RIOPS" "$DEV_WIOPS"
  _mdt_apply_tier_io "$majmin"
  _mdt_io_queue_flush || MDT_RC=1
  _rc=${MDT_IOQ_RC[0]}
  if [ "$_rc" = 0 ]; then
    # "authoritative" is only ever claimed after the kernel agreed
    log "dev.slice: io.max on $sd_dev (from $IO_DEV_PATH) is authoritative: ${DEV_RIOPS}r/${DEV_WIOPS}w IOPS, ${DEV_RBPS}/${DEV_WBPS} B/s r/w [$DEV_IO_SRC; DEV_IO_CAP_PCT=${DEV_IO_CAP_PCT}] — covers every dev.slice child combined"
  fi
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
  local majmin="$1" sd_dev pct="${DEV_SUBSLICE_IOPS_PCT:-60}" entry tier unit sub f sub_ri sub_wi touched
  local -A v bad
  local -a props
  for entry in $MDT_IO_TIERS; do
    tier=${entry%%:*}; unit=${entry#*:}
    sub=0; case "$unit" in dev-gates.slice|dev-buildkitd.slice) sub=1 ;; esac
    v=(); bad=()
    for f in RBPS WBPS RIOPS WIOPS; do
      # an INVALID pin is not "unset": it is omitted below (the last good value or
      # the fallback stays in force), logged ERROR by _mdt_tier_pin, and fails the run
      if ! v[$f]=$(_mdt_tier_pin "$tier" "$f"); then bad[$f]=1; v[$f]=""; MDT_RC=1; fi
    done
    if [ "$sub" = 1 ]; then
      sub_ri=$(( DEV_RIOPS * pct / 100 )); sub_wi=$(( DEV_WIOPS * pct / 100 ))
      if [ "$sub_ri" -lt 1 ] || [ "$sub_wi" -lt 1 ]; then
        log "WARN: derived sub-slice IOPS cap <= 0 — $unit keeps only dev.slice's shared ceiling"
      else
        { [ -z "${v[RIOPS]}" ] || [ "$sub_ri" -lt "${v[RIOPS]}" ]; } && v[RIOPS]=$sub_ri
        { [ -z "${v[WIOPS]}" ] || [ "$sub_wi" -lt "${v[WIOPS]}" ]; } && v[WIOPS]=$sub_wi
      fi
    fi
    touched="${v[RBPS]}${v[WBPS]}${v[RIOPS]}${v[WIOPS]}"
    # nothing configured for a tier that is not a sub-ceiling tier: leave it alone
    [ -n "$touched" ] || continue
    props=()
    sd_dev=$(_mdt_io_devname_for_majmin "$majmin" 2>&1) || {
      log "ERROR: $unit: cannot name the whole disk $majmin for systemd: $sd_dev — tier IO cap not applied"
      MDT_RC=1
      continue
    }
    for f in RBPS WBPS RIOPS WIOPS; do
      case "$f" in
        RBPS) key=IOReadBandwidthMax ;; WBPS) key=IOWriteBandwidthMax ;;
        RIOPS) key=IOReadIOPSMax ;; WIOPS) key=IOWriteIOPSMax ;;
      esac
      if [ -n "${bad[$f]:-}" ]; then
        continue                                  # invalid: omitted, never cleared
      elif [ -n "${v[$f]}" ]; then
        props+=("$key=" "$key=$sd_dev ${v[$f]}")   # reset + set in ONE call (MDT-HS-04)
      else
        props+=("$key=")                          # unset on a touched tier slice: drop our earlier value
        v[$f]=max                                 # ... and verify it reads unlimited
      fi
    done
    [ "${#props[@]}" -gt 0 ] || continue
    # shellcheck disable=SC2034  # read by _mdt_io_queue_add (sourced lib)
    MDT_IO_PROPS=("${props[@]}")
    _mdt_io_queue_add "$unit" "$CG/dev.slice/$unit" "$majmin" \
      "$([ -n "${bad[RBPS]:-}" ] || echo "${v[RBPS]}")" "$([ -n "${bad[WBPS]:-}" ] || echo "${v[WBPS]}")" \
      "$([ -n "${bad[RIOPS]:-}" ] || echo "${v[RIOPS]}")" "$([ -n "${bad[WIOPS]:-}" ] || echo "${v[WIOPS]}")"
  done
}

if [ -n "${IO_DEV_PATH:-}" ]; then
  [ "${MDT_IO_BASELINE_VALID:-0}" = 1 ] \
    || log "no current plausible baseline — dev.slice uses its pinned/static values until mdt-io-baseline.py succeeds"
  _mdt_apply_dev_io
  # a run that verified clean WITHOUT needing a reload ends the self-heal rate-limit window
  _mdt_io_stamp_clear_if_clean
else
  _mdt_no_device
fi

# --- swap coverage (D6) ----------------------------------------------------------
# Swap-out IO is charged to the page owner's cgroup (kernel behaviour; per kernel
# source, not measured here — bio_associate_blkg_from_page in mm/page_io.c), so a
# dev.slice io.max on the disk holding the swap bounds it; a swap disk with no
# entry is uncapped.  The warning is moot while dev.slice is inactive (no io.max
# file) and is logged ONCE per state change, not every minute.
if [ -r "${MDT_PROC_ROOT:-/proc}/swaps" ] && [ -e "$CG/dev.slice/io.max" ]; then
  _swap_now=$(_mdt_uncapped_swap_devices)
  _swap_state_file="${MDT_STATE_DIR:-/run}/mdt-uncapped-swap.state"
  _swap_before=$(cat "$_swap_state_file" 2>/dev/null || true)
  if [ "$_swap_now" != "$_swap_before" ]; then
    while read -r _swap_dev _swap_disk; do
      [ -n "$_swap_dev" ] || continue
      if [ "$_swap_disk" = unresolved ]; then
        log "WARN: swap device $_swap_dev: its whole disk could not be resolved — cannot confirm swap IO is capped"
      else
        log "WARN: UNCAPPED SWAP: $_swap_dev lies on disk $_swap_disk, which has NO dev.slice io.max entry — a dev container that swaps can saturate that disk (logged once per change)"
      fi
    done <<<"$_swap_now"
    [ -n "$_swap_now" ] || log "swap coverage: every active swap disk now has a dev.slice io.max entry"
    printf '%s' "$_swap_now" > "$_swap_state_file" 2>/dev/null || true
  fi
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
