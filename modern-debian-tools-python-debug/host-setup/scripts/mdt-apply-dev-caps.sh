#!/usr/bin/env bash
# mdt host-setup — runtime half of the dev-tier governance. Everything the
# static slice units can't express:
#   - dev.slice (the shared root) IO*Max at DEV_IO_CAP_PCT% of the MEASURED
#     device ceilings (io-baseline.env) — replaces the deliberately tight
#     unit-file statics for EVERY child combined (dev-interactive.slice,
#     dev-background.slice, dev-gates.slice, ... — host dev-tier cgroup
#     governance rollout: one absolute ceiling on the shared parent, not one
#     per child)
#   - every dev slice's memory.zswap.writeback (raw-write fallback for systemd
#     < 256 where the MemoryZSwapWriteback= directive doesn't exist; harmless
#     double-set on newer hosts)
#   - per-container caps: test-runner/buildx_buildkit_*/devcontainer scopes get
#     io.max at WATCHER_IO_CAP_PCT% of the baseline (bench+buildkit additionally
#     get IOWeight=1; the devcontainer does not — it is the IDE). This is the
#     ONLY placement-independent governance buildx_buildkit_* workers get at
#     all: Buildx's own cgroup-parent driver-opt is unreliable under the
#     systemd cgroup driver (docs/BUILD-ARCHITECTURE.md), so they can't be
#     PLACED under dev.slice — DEV_IO_CAP_PCT's aggregate never reaches them.
#     Docker scopes are also transient units: they exist only while the
#     container runs, so this can only ever be done at runtime, never
#     declaratively in a unit file. This sweep is the BACKSTOP:
#     mdt-io-cap-watcher.service applies the same caps within the same
#     second of container start via `docker events` — see
#     mdt-container-caps.lib.sh (shared by both) and host-setup/README.md
#     "Persistence model".
#   - cgroup2 mount-flag check: without memory_recursiveprot every slice-level
#     MemoryLow/MemoryMin silently stops protecting container pages
# Idempotent; tolerant of missing docker/baseline/slices. Config:
# /etc/mdt/host-setup.env (see host-setup.env.example). Run by
# mdt-host-slices.service at boot + mdt-host-slices.timer periodically.
set -uo pipefail
CG="${CG:-/sys/fs/cgroup}"
CONF="${CONF:-/etc/mdt/host-setup.env}"
log(){ echo "[mdt-dev-caps] $*"; }

# shellcheck source=./mdt-container-caps.lib.sh
. "$(dirname "$0")/mdt-container-caps.lib.sh"

# _mdt_load_config: sources $CONF, sets WATCHER_IO_CAP_PCT/*_PATTERNS/
# IO_BASELINE_ENV — shared with mdt-io-cap-watcher.sh, see the lib file.
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

# --- dev.slice: whole-estate measured IO caps (dev-interactive + dev-background) --
if [ -n "${RIOPS_MAX:-}" ]; then
  if [ -n "${WIOPS_MAX:-}" ] && [ -n "${RBW_MAX_BPS:-}" ] \
     && [ -n "${WBW_MAX_BPS:-}" ] && [ -n "${IO_DEV_PATH:-}" ]; then
    DEV_RIOPS=$(( RIOPS_MAX * DEV_IO_CAP_PCT / 100 ))
    DEV_WIOPS=$(( WIOPS_MAX * DEV_IO_CAP_PCT / 100 ))
    DEV_RBPS=$(( RBW_MAX_BPS * DEV_IO_CAP_PCT / 100 ))
    DEV_WBPS=$(( WBW_MAX_BPS * DEV_IO_CAP_PCT / 100 ))
    # 0 in io.max is not "unlimited" — it halts IO. Refuse a bad baseline and
    # leave the unit-file statics (tight, but a working host) in force.
    if [ "$DEV_RIOPS" -lt 1 ] || [ "$DEV_WIOPS" -lt 1 ] || [ "$DEV_RBPS" -lt 1 ] || [ "$DEV_WBPS" -lt 1 ]; then
      log "WARN: baseline yields a <= 0 estate cap — dev.slice keeps unit-file statics"
    # --runtime: survives daemon-reload (runtime drop-in), gone at reboot —
    # which is exactly right, this service re-runs at every boot.
    elif systemctl set-property --runtime dev.slice \
         "IOReadBandwidthMax=$IO_DEV_PATH $DEV_RBPS" "IOWriteBandwidthMax=$IO_DEV_PATH $DEV_WBPS" \
         "IOReadIOPSMax=$IO_DEV_PATH $DEV_RIOPS" "IOWriteIOPSMax=$IO_DEV_PATH $DEV_WIOPS" 2>/tmp/mdt-cg-err; then
      log "dev.slice: io.max=${DEV_RIOPS}r/${DEV_WIOPS}w IOPS $((DEV_RBPS/1048576))/$((DEV_WBPS/1048576))MB/s r/w (${DEV_IO_CAP_PCT}% of baseline — covers every dev.slice child combined)"
      # Per-tier IOPS sub-ceiling for dev-gates.slice/dev-buildkitd.slice:
      # tighter than the shared parent ceiling above, so
      # neither alone can claim the whole dev.slice IOPS budget when
      # interactive/background also want it. IOPS only — bandwidth is
      # deliberately left at whatever dev.slice's own cap allows (see each
      # unit's own comment for why). Runtime-only, same reason dev.slice's
      # own cap is: a static unit-file number can't express "a percentage of
      # what was just measured."
      DEV_SUBSLICE_IOPS_PCT="${DEV_SUBSLICE_IOPS_PCT:-60}"
      SUB_RIOPS=$(( DEV_RIOPS * DEV_SUBSLICE_IOPS_PCT / 100 ))
      SUB_WIOPS=$(( DEV_WIOPS * DEV_SUBSLICE_IOPS_PCT / 100 ))
      if [ "$SUB_RIOPS" -ge 1 ] && [ "$SUB_WIOPS" -ge 1 ]; then
        for _sub_slice in dev-gates.slice dev-buildkitd.slice; do
          if systemctl set-property --runtime "$_sub_slice" \
               "IOReadIOPSMax=$IO_DEV_PATH $SUB_RIOPS" "IOWriteIOPSMax=$IO_DEV_PATH $SUB_WIOPS" 2>/tmp/mdt-cg-err; then
            log "$_sub_slice: IOPS sub-ceiling ${SUB_RIOPS}r/${SUB_WIOPS}w (${DEV_SUBSLICE_IOPS_PCT}% of dev.slice's own — bandwidth untouched, inherits the parent's)"
          else
            log "WARN: $_sub_slice IOPS sub-ceiling set-property failed ($(cat /tmp/mdt-cg-err 2>/dev/null))"
          fi
        done
      else
        log "WARN: derived sub-slice IOPS cap <= 0 — dev-gates.slice/dev-buildkitd.slice keep only dev.slice's shared ceiling"
      fi
    else
      log "WARN: dev.slice set-property failed ($(cat /tmp/mdt-cg-err 2>/dev/null)) — unit-file statics remain in force"
    fi
  else
    log "baseline file incomplete or no device — dev.slice keeps unit-file statics"
  fi
else
  log "no $IO_BASELINE_ENV — dev.slice keeps unit-file statics (run mdt-io-baseline.py)"
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

# --- per-container caps: bench / buildkit / devcontainer ----------------------
# These target the transient docker-<id>.scope of each matched container —
# scopes exist only while the container runs. mdt-io-cap-watcher.service now
# applies this instantly on container start via `docker events`; this sweep
# is the backstop for whatever it missed (a restart window, a docker daemon
# restart mid-stream) — see host-setup/README.md "Persistence model". Uses
# the SAME _mdt_match/_mdt_apply_container_caps/_mdt_classify_and_apply as
# the watcher (mdt-container-caps.lib.sh), so the two can never drift apart.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  # Per-container ceilings: WATCHER_IO_CAP_PCT% of baseline, static fallbacks
  # when no baseline exists (tight on purpose — measure!). Applies to every
  # category this sweep matches below (test-runner/"bench", buildkit,
  # devcontainer) — one percentage, three container categories.
  _mdt_derive_watcher_caps
  for c in $([ "${WATCHER_SKIP:-0}" = 0 ] && docker ps -q 2>/dev/null); do
    _mdt_classify_and_apply "$c"
  done
else
  log "docker unavailable — skipped per-container sweep"
fi

log "done"
