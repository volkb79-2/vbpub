#!/usr/bin/env bash
# mdt host-setup — shared per-container IO-cap logic.
#
# Sourced by BOTH mdt-dev-governance-reconcile.sh (the periodic sweep, a backstop —
# see host-setup/README.md "Persistence model") and mdt-container-io-events-watcher.sh (the
# instant docker-events watcher). One definition of "how a container gets
# capped" instead of two copies that can drift apart.
#
# Callers must define `log()` before sourcing this, and must call
# `_mdt_load_config` then `_mdt_derive_watcher_caps` before `_mdt_apply_container_caps`.
# Not meant to be executed directly.

# _mdt_load_config: source $CONF, apply defaults.
_mdt_load_config() {
  # shellcheck disable=SC1090
  [ -f "$CONF" ] && . "$CONF" || log "WARN: $CONF not found — using built-in defaults"

  WATCHER_IO_CAP_PCT="${WATCHER_IO_CAP_PCT:-80}"
  WATCHER_BUILDKIT_NAME_PATTERNS="${WATCHER_BUILDKIT_NAME_PATTERNS:-buildx_buildkit_*}"
  IO_BASELINE_ENV="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"
  IO_BASELINE_TESTFILE="${IO_BASELINE_TESTFILE:-/var/lib/mdt/iocost-coef-fio.testfile}"
}

# _mdt_scope_path <container-id>: resolve the transient Docker scope while
# keeping the namespace-sensitive proc root injectable for tests.  The live
# default is /proc; tests use a private tree because a container cannot safely
# fabricate another process's cgroup membership.
_mdt_scope_path() {
  local cid="$1" pid scope_rel scope
  pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null) || return 1
  { [ -z "$pid" ] || [ "$pid" = "0" ]; } && return 1
  scope_rel=$(awk -F: '/^0::/{print $3; exit}' "${MDT_PROC_ROOT:-/proc}/$pid/cgroup" 2>/dev/null)
  [ -n "$scope_rel" ] && [ "$scope_rel" != / ] || return 1
  scope="${CG:-/sys/fs/cgroup}$scope_rel"
  # buildkitd nests sub-cgroups INSIDE its container; limits on the scope
  # cover the whole subtree.
  case "$scope" in *".scope/"*) scope="${scope%%.scope/*}.scope" ;; esac
  printf '%s\n' "$scope"
}

# _mdt_governed_slice <scope-path> -> the child slice owning this scope, or
# empty when placement is outside MDT's hierarchy.  Placement is the source
# of truth for ordinary containers; names and images are only used below for
# an out-of-tree Buildx worker, where placement itself is the fault.
_mdt_governed_slice() {
  case "$1" in
    "$CG/dev.slice/dev-interactive.slice/docker-"*.scope) printf '%s\n' dev-interactive.slice ;;
    "$CG/dev.slice/dev-background.slice/docker-"*.scope) printf '%s\n' dev-background.slice ;;
    "$CG/dev.slice/dev-gates.slice/docker-"*.scope) printf '%s\n' dev-gates.slice ;;
    "$CG/dev.slice/dev-buildkitd.slice/docker-"*.scope) printf '%s\n' dev-buildkitd.slice ;;
    "$CG/dev.slice/dev-memory_min_guaranteed.slice/docker-"*.scope) printf '%s\n' dev-memory_min_guaranteed.slice ;;
    *) return 1 ;;
  esac
}

# _mdt_discover_io_dev_path: sets IO_DEV_PATH if not already set. Deliberately
# does NOT strip partition/mapper indirection — caps on the partition node
# work; set IO_DEV_PATH yourself for the whole-disk node.
_mdt_discover_io_dev_path() {
  if [ -z "${IO_DEV_PATH:-}" ]; then
    local path _io_source
    for path in /var/lib/docker /; do
      _io_source=$(findmnt -no SOURCE --target "$path" 2>/dev/null || true)
      case "$_io_source" in
        /dev/?*) IO_DEV_PATH="$_io_source"; break ;;
      esac
    done
  fi
  [ -n "${IO_DEV_PATH:-}" ] && log "io device: $IO_DEV_PATH" \
    || log "WARN: no host /dev block-device node discovered (a container namespace may report overlay) — all IO cap steps will be skipped"
}

# _mdt_load_baseline: sources $IO_BASELINE_ENV if present, setting
# RIOPS_MAX/WIOPS_MAX/RBW_MAX_BPS/WBW_MAX_BPS/MEASURE_METHOD. Logs the same
# provenance warnings mdt-dev-governance-reconcile.sh always has (burst-v1 reads high on
# a VM; unrecognised/missing method means "unverified").
_mdt_load_baseline() {
  # This is deliberately a state bit, not inferred from whichever numeric
  # fields happened to survive a partial/invalid results file. The runtime root
  # cap must be cleared whenever the results are not current, so stale
  # measured io.max value cannot outlive the baseline that justified it.
  MDT_IO_BASELINE_VALID=0
  RIOPS_MAX="" WIOPS_MAX="" RBW_MAX_BPS="" WBW_MAX_BPS="" MEASURE_METHOD="" MEASURED_AT=""
  [ -f "$IO_BASELINE_ENV" ] || { log "no $IO_BASELINE_ENV — per-container IO caps are disabled until mdt-io-baseline.py succeeds"; return; }
  local _invalid=""
  # shellcheck disable=SC1090
  . "$IO_BASELINE_ENV" 2>/dev/null || true
  [ "${SCHEMA_VERSION:-}" = 3 ] || _invalid="${_invalid:-benchmark-results schema is ${SCHEMA_VERSION:-missing}, expected 3 (stale: measured by an older method that read through the hypervisor write-back cache; re-run mdt-io-baseline.py --force)}"
  [ "${MEASURE_METHOD:-}" = iocost-coef-gen ] || _invalid="${_invalid:-method is not iocost-coef-gen}"
  [ -n "${MEASURED_AT:-}" ] || _invalid="${_invalid:-MEASURED_AT is missing}"
  [ "${KERNEL_RELEASE:-}" = "$(uname -r)" ] || _invalid="${_invalid:-kernel release changed}"
  for _baseline_key in RIOPS_MAX WIOPS_MAX RBW_MAX_BPS WBW_MAX_BPS RBPS RSEQIOPS RRANDIOPS WBPS WSEQIOPS WRANDIOPS DEVNO TESTFILE_STAT_DEV DOCKER_STAT_DEV TESTFILE_SIZE_BYTES; do
    _baseline_value="${!_baseline_key:-}"
    case "$_baseline_value" in
      ''|*[!0-9]*) _invalid="${_invalid:-$_baseline_key is not a positive integer}" ;;
      0) _invalid="${_invalid:-$_baseline_key is zero}" ;;
    esac
  done
  [ -n "${TESTFILE:-}" ] || _invalid="${_invalid:-TESTFILE is missing}"
  if [ -z "$_invalid" ]; then
    _testfile_dev=$(stat -c %d "$TESTFILE" 2>/dev/null || true)
    _docker_dev=$(stat -c %d /var/lib/docker 2>/dev/null || true)
    [ -f "$TESTFILE" ] || _invalid="benchmark target is missing: $TESTFILE"
    [ "$(stat -c %s "$TESTFILE" 2>/dev/null || echo 0)" = "$TESTFILE_SIZE_BYTES" ] || _invalid="${_invalid:-benchmark target size changed}"
    [ "$_testfile_dev" = "$TESTFILE_STAT_DEV" ] || _invalid="benchmark target filesystem changed"
    [ "$_docker_dev" = "$DOCKER_STAT_DEV" ] || _invalid="Docker data filesystem changed"
    _source=$(findmnt -no SOURCE --target "$TESTFILE" 2>/dev/null || true)
    [ "$_source" = "$FINDMNT_SOURCE" ] || _invalid="benchmark target mount source changed"
  fi
  if [ -z "$_invalid" ]; then
    # The identity AND plausibility check lives in one place (the Python
    # script).  Its absence is NOT a pass: a baseline that cannot be checked
    # is not a verified baseline (absence-for-emptiness).
    local _baseline_py="${MDT_IO_BASELINE_PY:-/usr/local/sbin/mdt-io-baseline.py}" _check_msg
    export IO_BASELINE_ROTATIONAL_FLAG="${IO_BASELINE_ROTATIONAL_FLAG:-}"
    if [ ! -x "$_baseline_py" ]; then
      _invalid="$_baseline_py is not installed, so device identity and plausibility cannot be verified"
    elif ! _check_msg=$("$_baseline_py" --check-results \
          --output "$IO_BASELINE_ENV" --testfile "$IO_BASELINE_TESTFILE" 2>&1 >/dev/null); then
      _invalid="${_check_msg:-device identity is not current (run mdt-io-baseline.py)}"
      _invalid="${_invalid//$'\n'/ }"
    fi
  fi
  if [ -n "$_invalid" ]; then
    log "WARN: ignoring $IO_BASELINE_ENV — $_invalid; per-container IO caps are disabled until a current baseline exists"
    RIOPS_MAX="" WIOPS_MAX="" RBW_MAX_BPS="" WBW_MAX_BPS="" MEASURE_METHOD=""
  else
    MDT_IO_BASELINE_VALID=1
  fi
}

# _mdt_derive_watcher_caps: sets WATCHER_RBPS/WBPS/RIOPS/WIOPS/WATCHER_SRC (and
# WATCHER_SKIP=1 if the derived cap is unusable). Call after _mdt_load_config,
# _mdt_load_baseline and _mdt_discover_io_dev_path.
_mdt_derive_watcher_caps() {
  # A missing baseline is indeterminate, not a measurement.  A guessed
  # per-container cap can turn normal interactive IO into D-state stalls (the
  # live incident that motivated this distinction), so it is safer to leave
  # these transient properties unset until the official benchmark succeeds.
  WATCHER_RBPS=""; WATCHER_WBPS=""; WATCHER_RIOPS=""; WATCHER_WIOPS=""
  WATCHER_SRC="disabled — no current baseline; run mdt-io-baseline.py"
  WATCHER_SKIP=0
  # All four or none: a partial baseline would derive a 0 cap, and 0 in io.max
  # is not "unlimited", it stops the container's IO dead.
  if [ -n "${RIOPS_MAX:-}" ] && [ -n "${WIOPS_MAX:-}" ] \
     && [ -n "${RBW_MAX_BPS:-}" ] && [ -n "${WBW_MAX_BPS:-}" ]; then
    WATCHER_RIOPS=$(( RIOPS_MAX   * WATCHER_IO_CAP_PCT / 100 ))
    WATCHER_WIOPS=$(( WIOPS_MAX   * WATCHER_IO_CAP_PCT / 100 ))
    WATCHER_RBPS=$((  RBW_MAX_BPS * WATCHER_IO_CAP_PCT / 100 ))
    WATCHER_WBPS=$((  WBW_MAX_BPS * WATCHER_IO_CAP_PCT / 100 ))
    WATCHER_SRC="${WATCHER_IO_CAP_PCT}% of measured io.cost ceiling"
  else
    log "per-container IO caps: disabled — no current benchmark results; run mdt-io-baseline.py before enabling measured caps"
    WATCHER_SKIP=1
  fi
  if [ -z "${IO_DEV_PATH:-}" ]; then
    log "per-container IO caps: disabled — no host IO device was discovered"
    WATCHER_SRC="disabled — no host IO device"
    WATCHER_SKIP=1
  fi
  if [ "$WATCHER_SKIP" = 0 ] && {
       [ "$WATCHER_RIOPS" -lt 1 ] || [ "$WATCHER_WIOPS" -lt 1 ] \
       || [ "$WATCHER_RBPS" -lt 1 ] || [ "$WATCHER_WBPS" -lt 1 ];
     }; then
    log "WARN: derived per-container cap <= 0 (bad baseline?) — skipping container caps"
    WATCHER_SKIP=1
  fi
}

# _mdt_match "value" "pattern1 pattern2 ..." — unquoted pattern list on
# purpose: patterns SHOULD glob.
_mdt_match() {
  local v="$1" p
  # shellcheck disable=SC2254
  for p in $2; do case "$v" in $p) return 0 ;; esac; done
  return 1
}

# _mdt_apply_container_caps <container-id> <label> [deprioritize]
# deprioritize=1 (bench/buildkit): also drop the scope to the lowest IO
# weight, so it yields the device to everything else. NOT used for the
# devcontainer — that scope lives in dev-interactive.slice and IS the IDE:
# capping its peak rate is the goal, making it lose every IO race to a
# sibling is not.
_mdt_apply_container_caps() {
  local cid="$1" label="$2" deprio="${3:-0}" scope unit sd_dev props=()
  [ -n "${IO_DEV_PATH:-}" ] || { log "WARN: $label ($cid): no io device — skipped"; return 0; }
  scope=$(_mdt_scope_path "$cid")
  if [ -z "$scope" ]; then
    log "WARN: $label ($cid): no usable unified cgroup path — skipped; a later sweep will retry"
    return 0
  fi
  for _scope_attempt in 1 2 3 4 5 6 7 8 9 10; do
    [ -d "$scope" ] && break
    sleep 0.1
  done
  if [ ! -d "$scope" ]; then
    log "WARN: $label ($cid): cgroup scope $scope did not materialize — skipped; a later sweep will retry"
    return 0
  fi
  unit="${scope##*/}"
  if ! sd_dev=$(_mdt_io_systemd_dev "$IO_DEV_PATH" 2>&1); then
    log "WARN: $label ($cid): cannot resolve $IO_DEV_PATH to the whole disk systemd caps (${sd_dev}) — skipped"
    return 0
  fi
  [ "$deprio" = 1 ] && props+=(IOWeight=1)
  MDT_IO_PROPS=()
  _mdt_io_props "$sd_dev" "$WATCHER_RBPS" "$WATCHER_WBPS" "$WATCHER_RIOPS" "$WATCHER_WIOPS"
  props+=("${MDT_IO_PROPS[@]}")
  # set-property (not a raw io.max write): systemd re-applies its own recorded
  # properties to a scope on every daemon-reload, silently wiping raw writes.
  if systemctl set-property --runtime "$unit" "${props[@]}" 2>/dev/null; then
    log "$label ($cid): io.max on $sd_dev=${WATCHER_RIOPS}r/${WATCHER_WIOPS}w IOPS $((WATCHER_RBPS/1048576))/$((WATCHER_WBPS/1048576))MB/s r/w (${WATCHER_SRC})$([ "$deprio" = 1 ] && echo ', io.weight=1')"
  else
    log "WARN: $label ($cid): set-property failed — skipped"
    return 0
  fi
  # BFQ per-scope weight has no systemd property — raw write, and because
  # systemd does not manage this attribute the write survives daemon-reload.
  [ "$deprio" = 1 ] && { echo "default 1" > "$scope/io.bfq.weight" 2>/dev/null || true; }
  return 0
}

# Remove only the IO properties this library owns from currently governed
# containers and explicitly matched out-of-tree Buildx workers. This is
# required when results become invalid or the host device disappears: simply
# skipping the next apply would leave a transient io.max behind indefinitely.
_mdt_clear_container_caps() {
  local cid name scope unit slice deprio label props=()
  for cid in $(docker ps -q 2>/dev/null); do
    scope=$(_mdt_scope_path "$cid")
    slice=$(_mdt_governed_slice "$scope" 2>/dev/null || true)
    deprio=0
    if [ -n "$slice" ]; then
      label="slice:$slice"
      case "$slice" in dev-gates.slice|dev-buildkitd.slice) deprio=1 ;; esac
    else
      name=$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | tr -d '/' || true)
      if ! _mdt_match "$name" "$WATCHER_BUILDKIT_NAME_PATTERNS"; then
        continue
      fi
      label="out-of-tree-buildkit:$name"
      deprio=1
    fi
    [ -n "$scope" ] || { log "WARN: $label ($cid): no usable cgroup scope while clearing stale IO caps"; continue; }
    [ -d "$scope" ] || { log "WARN: $label ($cid): cgroup scope $scope is not present while clearing stale IO caps"; continue; }
    unit="${scope##*/}"
    props=(IOReadBandwidthMax= IOWriteBandwidthMax= IOReadIOPSMax= IOWriteIOPSMax=)
    [ "$deprio" = 1 ] && props+=(IOWeight=)
    if systemctl set-property --runtime "$unit" "${props[@]}" 2>/dev/null; then
      # io.bfq.weight is not a systemd property. Restore the normal default
      # after removing MDT's bench/buildkit deprioritisation.
      [ "$deprio" = 1 ] && printf 'default 100\n' > "$scope/io.bfq.weight" 2>/dev/null || true
      log "$label ($cid): cleared MDT-owned transient IO caps"
    else
      log "WARN: $label ($cid): failed to clear MDT-owned transient IO caps"
    fi
  done
}

# _mdt_classify_and_apply <container-id> — applies to every container whose
# scope is under a governed child. For a scope outside that tree, only a name
# matching WATCHER_BUILDKIT_NAME_PATTERNS is eligible; this is the explicit
# safety boundary for a mis-placed Buildx worker.
_mdt_classify_and_apply() {
  local cid="$1" name scope slice deprio
  [ "${WATCHER_SKIP:-0}" = 1 ] && return 0
  scope=$(_mdt_scope_path "$cid")
  slice=$(_mdt_governed_slice "$scope" 2>/dev/null || true)
  if [ -n "$slice" ]; then
    deprio=0
    case "$slice" in dev-gates.slice|dev-buildkitd.slice) deprio=1 ;; esac
    _mdt_apply_container_caps "$cid" "slice:$slice" "$deprio"
    return 0
  fi
  name=$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | tr -d '/' || true)
  if _mdt_match "$name" "$WATCHER_BUILDKIT_NAME_PATTERNS"; then
    _mdt_apply_container_caps "$cid" "out-of-tree-buildkit:$name" 1
  fi
}

# ---------------------------------------------------------------------------
# dev.slice IO governance helpers (MDT-HS-01).  Shared by
# mdt-dev-governance-reconcile.sh, install.sh (static fallback rendering) and
# check.sh, so "what cap is intended" and "what the kernel really holds" have
# one definition each.  Test seams: MDT_SYSFS_ROOT (default /sys) and
# MDT_PROC_ROOT (default /proc).
# ---------------------------------------------------------------------------

# _mdt_size_to_bytes <value>: a positive integer with an optional K/M/G/T
# suffix (base 1024, like every other size in host-setup) -> byte count on
# stdout.  Returns 1 for anything else, and for 0: in io.max a 0 does not mean
# unlimited, it halts IO.  Bytes are what we hand to systemd and to the unit
# file, so systemd never applies its own suffix base to an MDT number.
_mdt_size_to_bytes() {
  local v="$1" num mult=1
  [[ "$v" =~ ^([0-9]+)([KMGT]?)$ ]] || return 1
  # bound BEFORE any arithmetic: bash wraps silently (18446744073709551621 -> 5,
  # an effective halt).  At most 18 digits, and number * multiplier <= 2^62.
  [ "${#BASH_REMATCH[1]}" -le 18 ] || return 1
  num=$((10#${BASH_REMATCH[1]}))
  case "${BASH_REMATCH[2]}" in
    K) mult=1024 ;; M) mult=$((1024**2)) ;; G) mult=$((1024**3)) ;; T) mult=$((1024**4)) ;;
  esac
  [ "$num" -ge 1 ] || return 1
  [ "$num" -le $((4611686018427387904 / mult)) ] || return 1
  printf '%s\n' $((num * mult))
}

# _mdt_resolve_static_io: sets MDT_STATIC_RBPS/WBPS/RIOPS/WIOPS (numbers) from
# ONE source: the operator-pinned DEV_IO_*_MAX key when set, else the
# DEV_STATIC_* key, else the built-in default.  install.sh renders the unit
# file's static lines from these and the reconcile asserts them at runtime, so
# the two can never disagree.  An invalid value is an ERROR (rc 1) and that
# field uses the built-in default so the slice stays capped; the caller
# decides whether rc 1 refuses (install.sh) or fails the run (reconcile).
_mdt_resolve_static_io() {
  local rc=0 field builtin abs static value
  for field in "RBPS DEV_IO_RBPS_MAX DEV_STATIC_RBW 150M" "WBPS DEV_IO_WBPS_MAX DEV_STATIC_WBW 150M" \
               "RIOPS DEV_IO_RIOPS_MAX DEV_STATIC_RIOPS 500" "WIOPS DEV_IO_WIOPS_MAX DEV_STATIC_WIOPS 500"; do
    set -- $field
    abs="${!2:-}"; static="${!3:-}"; builtin="$4"
    if [ -n "$abs" ]; then
      if ! value=$(_mdt_size_to_bytes "$abs"); then
        log "ERROR: $2='$abs' is not a positive integer with an optional K/M/G/T suffix (0 would halt IO) — ignoring it"
        rc=1; abs=""
      fi
    fi
    if [ -z "$abs" ]; then
      if [ -z "$static" ] || ! value=$(_mdt_size_to_bytes "$static"); then
        [ -n "$static" ] && { log "ERROR: $3='$static' is not a positive integer with an optional K/M/G/T suffix — using built-in $builtin"; rc=1; }
        value=$(_mdt_size_to_bytes "$builtin")
      fi
    fi
    printf -v "MDT_STATIC_$1" '%s' "$value"
  done
  return $rc
}

# _mdt_resolve_dev_io_caps: sets DEV_RBPS/WBPS/RIOPS/WIOPS and DEV_IO_SRC.
# Per field: the operator-pinned DEV_IO_*_MAX when set, else DEV_IO_CAP_PCT of
# a VALID baseline, else the static fallback; pinned AND baseline -> the min.
# Returns 1 when a configured key was invalid (already logged).
_mdt_resolve_dev_io_caps() {
  local rc=0 field abs base value pinned_value src
  _mdt_resolve_static_io || rc=1
  DEV_IO_SRC=""
  for field in "RBPS DEV_IO_RBPS_MAX RBW_MAX_BPS" "WBPS DEV_IO_WBPS_MAX WBW_MAX_BPS" \
               "RIOPS DEV_IO_RIOPS_MAX RIOPS_MAX" "WIOPS DEV_IO_WIOPS_MAX WIOPS_MAX"; do
    set -- $field
    abs="${!2:-}"; base=""
    pinned_value=""
    [ -n "$abs" ] && pinned_value=$(_mdt_size_to_bytes "$abs" 2>/dev/null || true)
    if [ "${MDT_IO_BASELINE_VALID:-0}" = 1 ] && [ -n "${!3:-}" ]; then
      base=$(( ${!3} * ${DEV_IO_CAP_PCT:-60} / 100 ))
      if [ "$base" -lt 1 ]; then
        log "ERROR: ${DEV_IO_CAP_PCT:-60}% of baseline $3=${!3} is below 1 — ignoring the baseline for $1"
        rc=1; base=""
      fi
    fi
    if [ -n "$pinned_value" ] && [ -n "$base" ]; then
      if [ "$base" -lt "$pinned_value" ]; then value=$base; src="baseline"; else value=$pinned_value; src="pinned"; fi
    elif [ -n "$pinned_value" ]; then value=$pinned_value; src="pinned"
    elif [ -n "$base" ]; then value=$base; src="baseline"
    else eval "value=\$MDT_STATIC_$1"; src="static"
    fi
    printf -v "DEV_$1" '%s' "$value"
    DEV_IO_SRC="$DEV_IO_SRC $1=$src"
  done
  DEV_IO_SRC="${DEV_IO_SRC# }"
  return $rc
}

# _mdt_io_devname_for_majmin <maj:min>: the devtmpfs node of that whole disk, `/dev/<DEVNAME>`,
# read from sysfs `dev/block/<maj:min>/uevent`.  This is THE spelling handed to systemd's IO*Max=
# properties (MDT-HS-04/H-34): systemd resolves a /dev node it can stat, the kernel name is
# stable across dynamic major renumbering (virtio 254 vs device-mapper 253 can swap between
# kernels or initramfs changes, which a rendered MAJ:MIN would not survive), and it exists
# before udev creates /dev/block symlinks.  Refuses (rc 1, reason on stderr) when DEVNAME is
# missing or the node does not exist -- never invents a name.
_mdt_io_devname_for_majmin() {
  local mm="$1" sysroot="${MDT_SYSFS_ROOT:-/sys}" name
  name=$(sed -n 's/^DEVNAME=//p' "$sysroot/dev/block/$mm/uevent" 2>/dev/null | head -n1)
  if [ -z "$name" ]; then
    echo "sysfs has no DEVNAME for block device $mm ($sysroot/dev/block/$mm/uevent)" >&2
    return 1
  fi
  printf '/dev/%s\n' "$name"
}

# _mdt_io_systemd_dev <device-path>: resolve to the originating WHOLE disk (_mdt_dev_majmin, the
# value the kernel io.max is keyed on and the read-back verifies), then its /dev node.  Never a
# mapper or partition node (measured 2026-10-20, systemd 257: IOReadBandwidthMax=/dev/mapper/<lv>
# left dev.slice's io.max empty while the whole-disk node wrote at once).
_mdt_io_systemd_dev() {
  local mm
  mm=$(_mdt_dev_majmin "$1") || return 1
  _mdt_io_devname_for_majmin "$mm"
}

# _mdt_io_props <systemd-dev> <rbps> <wbps> <riops> <wiops>: appends the IO set-property
# arguments to the array MDT_IO_PROPS.  Every key is RESET first and then set, in the
# SAME call (MDT-016: an empty assignment alone wipes the unit-file value, so the reset
# must travel with the set), so a stale entry under another spelling of the same disk
# (/dev/vda from an earlier pin, a mapper path from an older reconcile) cannot shadow
# the intended cap: the kernel took the LAST entry's value (measured, MDT-HS-04).
# An empty value emits only the reset.
_mdt_io_props() {
  local sd="$1"; shift
  local key val
  for key in IOReadBandwidthMax IOWriteBandwidthMax IOReadIOPSMax IOWriteIOPSMax; do
    val="$1"; shift
    MDT_IO_PROPS+=("$key=")
    [ -z "$val" ] || MDT_IO_PROPS+=("$key=$sd $val")
  done
}

# _mdt_dev_majmin <device-path>: the MAJ:MIN of the device the kernel io.max
# is keyed on — the originating WHOLE disk.  systemd resolves a mapper node
# (single slave) and a partition to that disk, so the kernel never shows the
# node named in the unit.  Prints it, or returns 1 (and prints nothing) when
# it cannot be determined, including a node with several slaves.
_mdt_dev_majmin() {
  local path="$1" hex majmin dir real slaves n
  local sysroot="${MDT_SYSFS_ROOT:-/sys}"
  hex=$(stat -L -c '%t:%T' "$path" 2>/dev/null) || return 1
  majmin="$((16#${hex%%:*})):$((16#${hex##*:}))"
  for _ in 1 2 3 4 5 6 7 8; do
    dir="$sysroot/dev/block/$majmin"
    [ -e "$dir" ] || return 1
    real=$(readlink -f "$dir")
    if [ -d "$real/slaves" ]; then
      slaves=("$real"/slaves/*)
      n=0; [ -e "${slaves[0]}" ] && n=${#slaves[@]}
      if [ "$n" -eq 1 ]; then
        majmin=$(cat "${slaves[0]}/dev" 2>/dev/null) || return 1
        continue
      elif [ "$n" -gt 1 ]; then
        # several backing devices (md RAID, an LV spanning PVs): systemd caps ONE
        # disk, so a cap here cannot be verified against one MAJ:MIN.  Refuse
        # with one clear reason rather than guess a member.
        echo "$path is backed by $n backing devices ($(printf '%s ' "${slaves[@]##*/}" | sed 's/ $//')); IO caps cannot be applied or verified on a multi-device node, so the unit-file static values stay the only cap" >&2
        return 1
      fi
    fi
    if [ -e "$real/partition" ]; then
      majmin=$(cat "$real/../dev" 2>/dev/null) || return 1
      continue
    fi
    printf '%s\n' "$majmin"
    return 0
  done
  return 1
}

# _mdt_io_max_fields <cgroup-dir> <maj:min>: prints "rbps wbps riops wiops"
# ("max" where the kernel prints no limit) for that device.  rc 1 = no file,
# or no line for the device.  cgroupfs files report size 0, so this reads the
# CONTENT; a size test on a cgroup control file is always false.
_mdt_io_max_fields() {
  local file="$1/io.max" majmin="$2" content mm rest kv rbps=max wbps=max riops=max wiops=max found=1
  content=$(cat "$file" 2>/dev/null) || return 1
  while read -r mm rest; do
    [ "$mm" = "$majmin" ] || continue
    found=0
    for kv in $rest; do
      case "$kv" in rbps=*) rbps=${kv#*=} ;; wbps=*) wbps=${kv#*=} ;; riops=*) riops=${kv#*=} ;; wiops=*) wiops=${kv#*=} ;; esac
    done
  done <<<"$content"
  [ "$found" = 0 ] || return 1
  printf '%s %s %s %s\n' "$rbps" "$wbps" "$riops" "$wiops"
}

# _mdt_verify_io_max <label> <cgroup-dir> <maj:min> <rbps> <wbps> <riops> <wiops>
# An empty intended value means "not intended; do not compare".  Returns
#   0  the kernel holds exactly the intended values (logged as such)
#   1  MISMATCH or missing entry: logs ERROR with what is ACTUALLY applied
#   2  the cgroup does not exist (slice not active): nothing is governed, so
#      this is indeterminate, not a pass and not a failure (logged)
_mdt_verify_io_max() {
  local label="$1" dir="$2" majmin="$3" want_rb="$4" want_wb="$5" want_ri="$6" want_wi="$7" got
  local lvl=ERROR remedy=" Remedy: ${MDT_VERIFY_REMEDY:-$_MDT_IO_OTHER_CAUSES}."
  [ "${MDT_VERIFY_SOFT:-0}" = 1 ] && lvl=WARN
  if [ ! -d "$dir" ]; then
    log "$label: cgroup not active yet — properties recorded but NOT verified (no process is governed until the slice starts; the next run verifies)"
    return 2
  fi
  if ! got=$(_mdt_io_max_fields "$dir" "$majmin"); then
    if { [ -z "$want_rb" ] || [ "$want_rb" = max ]; } && { [ -z "$want_wb" ] || [ "$want_wb" = max ]; } \
       && { [ -z "$want_ri" ] || [ "$want_ri" = max ]; } && { [ -z "$want_wi" ] || [ "$want_wi" = max ]; }; then
      log "$label: verified kernel io.max has no limit for $majmin (cleared values read as unlimited)"
      return 0
    fi
    log "$lvl: $label: kernel io.max has NO entry for $majmin (content: '$(tr '\n' ';' < "$dir/io.max" 2>/dev/null)') — the intended cap is NOT in effect.$remedy"
    return 1
  fi
  set -- $got
  if { [ -z "$want_rb" ] || [ "$1" = "$want_rb" ]; } && { [ -z "$want_wb" ] || [ "$2" = "$want_wb" ]; } \
     && { [ -z "$want_ri" ] || [ "$3" = "$want_ri" ]; } && { [ -z "$want_wi" ] || [ "$4" = "$want_wi" ]; }; then
    log "$label: verified kernel io.max $majmin rbps=$1 wbps=$2 riops=$3 wiops=$4"
    return 0
  fi
  log "$lvl: $label: kernel io.max $majmin is rbps=$1 wbps=$2 riops=$3 wiops=$4 but intended rbps=${want_rb:--} wbps=${want_wb:--} riops=${want_ri:--} wiops=${want_wi:--} — the intended cap is NOT in effect.$remedy"
  return 1
}

# _mdt_systemd_reports_io <unit> <rbps> <wbps> <riops> <wiops>: true when `systemctl show` lists EVERY
# intended non-empty value, i.e. systemd BELIEVES the whole cap is set (one value among several is not
# enough: a partial report is a different problem than a stale per-spelling entry).
_mdt_systemd_reports_io() {
  local unit="$1" shown v n=0
  shown=$(systemctl show "$unit" -p IOReadBandwidthMax -p IOWriteBandwidthMax -p IOReadIOPSMax -p IOWriteIOPSMax 2>/dev/null) || return 1
  shift
  for v in "$@"; do
    case "$v" in ''|max) continue ;; esac
    n=$((n + 1))
    grep -Eq "(^|[ =])$v( |\$)" <<<"$shown" || return 1
  done
  [ "$n" -gt 0 ]
}

# The self-heal reload is rate-limited ACROSS runs: the timer starts a fresh process every minute, so a
# per-process flag would let a heal that does not help daemon-reload PID 1 forever.  A stamp file holds
# "<epoch> <reason>"; no reload while the last one is younger than the window.  The window is a documented
# constant (30 min), not a knob: it only bounds how often PID 1 is asked to reload for this one cause.
MDT_IO_RELOAD_WINDOW_SECONDS=1800
_mdt_io_stamp_file() { printf '%s/io-selfheal-reload.stamp\n' "${MDT_STATE_DIR:-/run/mdt}"; }
_mdt_now() { printf '%s\n' "${MDT_FAKE_NOW:-$(date +%s)}"; }

# _mdt_io_reload_allowed: rc 0 = may reload; rc 1 = suppressed (sets MDT_IO_SUPPRESS_INFO with when/why).
_mdt_io_reload_allowed() {
  local f last reason now
  f=$(_mdt_io_stamp_file)
  [ -e "$f" ] || return 0
  read -r last reason < "$f" 2>/dev/null || last=""
  case "$last" in ''|*[!0-9]*) return 0 ;; esac    # an unreadable/garbled stamp does not block a heal
  now=$(_mdt_now)
  if [ $((now - last)) -lt "$MDT_IO_RELOAD_WINDOW_SECONDS" ]; then
    MDT_IO_SUPPRESS_INFO="last self-heal reload at $(date -u -d "@$last" +%FT%TZ 2>/dev/null || echo "$last") ($((now - last))s ago, window ${MDT_IO_RELOAD_WINDOW_SECONDS}s${reason:+, reason: $reason}) did not fix it"
    return 1
  fi
  return 0
}

# _mdt_io_stamp_write <reason>: records the reload; failing to record is refused (fail closed:
# an unrecorded reload could not be rate-limited).
_mdt_io_stamp_write() {
  local f; f=$(_mdt_io_stamp_file)
  mkdir -p "$(dirname "$f")" 2>/dev/null && printf '%s %s\n' "$(_mdt_now)" "$1" > "$f" 2>/dev/null
}

# _mdt_io_stamp_clear_if_clean: called once at the end of a run that needed NO reload and failed nothing.
_mdt_io_stamp_clear_if_clean() {
  [ "${MDT_IO_RELOADED:-0}" = 1 ] && return 0
  [ "${MDT_RC:-0}" = 0 ] || return 0
  rm -f "$(_mdt_io_stamp_file)" 2>/dev/null || true
}

_MDT_IO_OTHER_CAUSES="check the other causes: the io controller not enabled for dev.slice or its parent, the wrong device (IO_DEV_PATH / whole disk), a kernel refusal of the io.max write (dmesg)"

# --- batch apply + verify with ONE self-heal reload per run (MDT-HS-04, MDT-HS-06) --------------
# systemd keeps one in-memory io-device-limit entry per PATH STRING; an empty assignment resets an entry
# to max but does not delete it, `systemctl show` and the runtime drop-in hide all-max entries, and the
# apply writes every entry in list order, so a stale all-max entry for another spelling of the same disk
# silently writes `max` over the cap (inferred from measurements and the v257 source).  `daemon-reload`
# rebuilds the list from the drop-in files, which hold only the non-max entries.
#
# MDT-HS-06 (live incident 2026-10-20 10:00 UTC): the spelling migration left stale entries on SEVERAL
# units.  Healing one unit and then verifying the next failed it, because the next unit's reset had
# happened AFTER the reload and (as measured) a reload does not reliably re-push units applied earlier in
# the run.  So the unit of work is the whole batch: apply EVERY managed unit, verify ALL, and only then,
# if any disagrees while systemd reports its intended values, reload ONCE (persisted limiter across runs,
# MDT_IO_RELOAD_WINDOW_SECONDS), re-apply EVERY unit and re-verify ALL.  A mismatch that survives the
# one reload plus re-apply is the failure.  A single run never fails a unit because of its own reload.
MDT_IOQ_UNITS=(); MDT_IOQ_DIRS=(); MDT_IOQ_MM=(); MDT_IOQ_WANT=(); MDT_IOQ_PROPS=(); MDT_IOQ_RC=()

_mdt_io_queue_reset() { MDT_IOQ_UNITS=(); MDT_IOQ_DIRS=(); MDT_IOQ_MM=(); MDT_IOQ_WANT=(); MDT_IOQ_PROPS=(); MDT_IOQ_RC=(); }

# _mdt_io_queue_apply <index>: set-property of the recorded properties.  rc 1 = failed (logged).
_mdt_io_queue_apply() {
  local i="$1" err
  local -a props
  mapfile -t props <<<"${MDT_IOQ_PROPS[$i]}"
  if ! err=$(systemctl set-property --runtime "${MDT_IOQ_UNITS[$i]}" "${props[@]}" 2>&1); then
    log "ERROR: ${MDT_IOQ_UNITS[$i]} IO cap set-property failed ($err)"
    return 1
  fi
}

# _mdt_io_queue_add <unit> <cgroup-dir> <maj:min> <rbps> <wbps> <riops> <wiops>: properties in the array
# MDT_IO_PROPS.  Applies now (order preserved) and records the unit for the batch verification.
_mdt_io_queue_add() {
  local i=${#MDT_IOQ_UNITS[@]}
  MDT_IOQ_UNITS[i]="$1"; MDT_IOQ_DIRS[i]="$2"; MDT_IOQ_MM[i]="$3"; MDT_IOQ_WANT[i]="$4 $5 $6 $7"
  MDT_IOQ_PROPS[i]=$(printf '%s\n' "${MDT_IO_PROPS[@]}")
  MDT_IOQ_RC[i]=0
  _mdt_io_queue_apply "$i" || MDT_IOQ_RC[i]=1
}

# _mdt_io_queue_verify <index> <soft 0|1>: sets MDT_IOQ_RC[index] from the kernel read-back (an apply that
# already failed stays failed).  MDT_VERIFY_REMEDY is honoured by the caller's environment.
_mdt_io_queue_verify() {
  local i="$1" soft="$2" rb wb ri wi
  read -r rb wb ri wi <<<"${MDT_IOQ_WANT[$i]}"
  MDT_VERIFY_SOFT="$soft" _mdt_verify_io_max "${MDT_IOQ_UNITS[$i]}" "${MDT_IOQ_DIRS[$i]}" "${MDT_IOQ_MM[$i]}" "$rb" "$wb" "$ri" "$wi"
  MDT_IOQ_RC[i]=$?
}

# _mdt_io_queue_flush: verify the whole batch, heal at most once, leave each unit's result in MDT_IOQ_RC
# (0 verified, 1 NOT in effect, 2 not verifiable).  rc 1 when any unit is 1.
_mdt_io_queue_flush() {
  local n=${#MDT_IOQ_UNITS[@]} i err rb wb ri wi failed=0 reporting=0 bad_units=""
  local -a applied_ok
  for ((i = 0; i < n; i++)); do
    if [ "${MDT_IOQ_RC[$i]}" = 1 ]; then applied_ok[i]=0; continue; fi    # its set-property failed (logged)
    applied_ok[i]=1
    _mdt_io_queue_verify "$i" 1
  done
  for ((i = 0; i < n; i++)); do
    [ "${MDT_IOQ_RC[$i]}" = 1 ] || continue
    bad_units="$bad_units ${MDT_IOQ_UNITS[$i]}"
    [ "${applied_ok[$i]}" = 1 ] || continue
    read -r rb wb ri wi <<<"${MDT_IOQ_WANT[$i]}"
    ! _mdt_systemd_reports_io "${MDT_IOQ_UNITS[$i]}" "$rb" "$wb" "$ri" "$wi" || reporting=1
  done
  if [ -z "$bad_units" ]; then return 0; fi
  # nothing to heal if systemd itself does not report the intended values for any failing unit
  if [ "$reporting" = 0 ]; then
    for ((i = 0; i < n; i++)); do
      [ "${MDT_IOQ_RC[$i]}" = 1 ] && [ "${applied_ok[$i]}" = 1 ] || continue
      MDT_VERIFY_REMEDY="systemd does not report all the intended values either, so this is not the stale-entry case and no reload was attempted; $_MDT_IO_OTHER_CAUSES" _mdt_io_queue_verify "$i" 0
    done
  elif ! _mdt_io_reload_allowed; then
    for ((i = 0; i < n; i++)); do
      [ "${MDT_IOQ_RC[$i]}" = 1 ] && [ "${applied_ok[$i]}" = 1 ] || continue
      MDT_VERIFY_REMEDY="reload suppressed: $MDT_IO_SUPPRESS_INFO; $_MDT_IO_OTHER_CAUSES" _mdt_io_queue_verify "$i" 0
    done
  else
    MDT_IO_RELOADED=1
    if ! _mdt_io_stamp_write "units=${bad_units# } kernel-disagrees-with-show"; then
      log "ERROR: cannot record the self-heal reload under $(dirname "$(_mdt_io_stamp_file)") — refusing to reload without a rate limit; fix that directory and rerun"
      for ((i = 0; i < n; i++)); do [ "${MDT_IOQ_RC[$i]}" = 1 ] && [ "${applied_ok[$i]}" = 1 ] && MDT_IOQ_RC[i]=1; done
    else
      log "WARN:${bad_units}: systemctl show reports the intended IO limits but the kernel io.max disagrees — a stale per-spelling limit entry inside systemd (invisible in show) overrides it; running ONE 'systemctl daemon-reload' to rebuild systemd's list from its drop-ins, then re-applying EVERY managed unit and re-verifying all of them (at most once per ${MDT_IO_RELOAD_WINDOW_SECONDS}s across runs)"
      if ! err=$(systemctl daemon-reload 2>&1); then
        log "ERROR: systemctl daemon-reload failed ($err) — the intended caps on${bad_units} are NOT in effect; fix the daemon-reload failure and rerun"
        for ((i = 0; i < n; i++)); do [ "${MDT_IOQ_RC[$i]}" = 1 ] && [ "${applied_ok[$i]}" = 1 ] && MDT_IOQ_RC[i]=1; done
      else
        for ((i = 0; i < n; i++)); do
          if ! _mdt_io_queue_apply "$i"; then MDT_IOQ_RC[i]=1; applied_ok[i]=0; continue; fi
          applied_ok[i]=1
          MDT_VERIFY_REMEDY="the stale-entry heal (one daemon-reload, then every managed unit re-applied) was tried and did not fix it; $_MDT_IO_OTHER_CAUSES" _mdt_io_queue_verify "$i" 0
        done
      fi
    fi
  fi
  for ((i = 0; i < n; i++)); do [ "${MDT_IOQ_RC[$i]}" = 1 ] && failed=1; done
  return "$failed"
}

# _mdt_active_swap_count: number of active swap devices that have a disk behind
# them (zram and other memory-backed devices do not count).
_mdt_active_swap_count() {
  local proc="${MDT_PROC_ROOT:-/proc}" dev rest n=0
  [ -r "$proc/swaps" ] || { echo 0; return 0; }
  while read -r dev rest; do
    [ "$dev" = Filename ] && continue
    case "$dev" in */zram*|*/ram[0-9]*) continue ;; esac
    [ -n "$dev" ] && n=$((n + 1))
  done < "$proc/swaps"
  echo "$n"
}

# _mdt_uncapped_swap_devices: for every active swap device on a real disk,
# prints "<swap-device> <maj:min-of-its-whole-disk>" when dev.slice's io.max
# has no entry for that disk (swap-out is charged to the page's memcg, so a
# cap on the disk covers it; no entry = uncapped swap IO).  A swap device
# whose disk cannot be resolved prints "<swap-device> unresolved".  zram and
# other memory-backed devices have no disk and are skipped.
_mdt_uncapped_swap_devices() {
  local proc="${MDT_PROC_ROOT:-/proc}" dev type rest mm capped devpath
  capped=$(awk '{print $1}' "${CG:-/sys/fs/cgroup}/dev.slice/io.max" 2>/dev/null)
  while read -r dev type rest; do
    [ "$dev" = Filename ] && continue
    case "$dev" in */zram*|*/ram[0-9]*) continue ;; esac
    devpath="$dev"
    if [ "$type" = file ]; then
      devpath=$(findmnt -no SOURCE --target "$dev" 2>/dev/null || true)
      devpath=${devpath%%\[*}   # btrfs: /dev/vda2[/@swap] -> /dev/vda2
      case "$devpath" in /dev/?*) ;; *) printf '%s unresolved\n' "$dev"; continue ;; esac
    fi
    if ! mm=$(_mdt_dev_majmin "$devpath" 2>/dev/null); then
      printf '%s unresolved\n' "$dev"; continue
    fi
    grep -qx "$mm" <<<"$capped" || printf '%s %s\n' "$dev" "$mm"
  done < "$proc/swaps"
}

# Per-tier absolute IO keys (runtime, read by the reconcile; amendment A1/D7).
# DEV_<TIER>_IO_{RBPS,WBPS,RIOPS,WIOPS}_MAX for each child slice.  The gate
# tier is the one that matters: swap stays ample, and a gate that swaps is
# throttled by ITS OWN IO cap because swap-out IO is charged to the page
# owner's cgroup — it cannot thrash the shared disk.
# shellcheck disable=SC2034  # consumed by mdt-dev-governance-reconcile.sh
MDT_IO_TIERS="INTERACTIVE:dev-interactive.slice BACKGROUND:dev-background.slice GATES:dev-gates.slice BUILDKITD:dev-buildkitd.slice"

# _mdt_tier_pin <TIER> <RBPS|WBPS|RIOPS|WIOPS>: prints the validated byte/IOPS
# count of DEV_<TIER>_IO_<FIELD>_MAX, nothing when unset.  rc 1 (after logging
# an ERROR, printing nothing) when set but invalid, so the caller keeps the
# tier capped by what remains and fails the run.
_mdt_tier_pin() {
  local key="DEV_${1}_IO_${2}_MAX" raw value
  raw="${!key:-}"
  [ -n "$raw" ] || return 0
  if ! value=$(_mdt_size_to_bytes "$raw"); then
    log "ERROR: $key='$raw' is not a positive integer with an optional K/M/G/T suffix (0 would halt IO) — ignoring it" >&2
    return 1
  fi
  printf '%s\n' "$value"
}

# Keys that are RUNTIME-ONLY and optional: an absent line means "not set" (a
# policy default), never "silently unbounded".  The 20 per-tier/dev.slice IO pins
# and the rotational-flag override qualify.  Render-time keys do not: an absent
# line drops a rendered directive and leaves a slice unbounded, so install.sh
# keeps refusing for those.
# The key set is defined ONCE, in host-setup/runtime-optional-keys.re, which the strict
# Python validator reads too (MDT-HS-02: two copies diverged in effect).
_mdt_runtime_optional_re() {
  local file="${MDT_RUNTIME_OPTIONAL_FILE:-$(dirname "${BASH_SOURCE[0]}")/../runtime-optional-keys.re}"
  local patterns
  patterns=$(grep -vE '^[[:space:]]*(#|$)' "$file" 2>/dev/null)
  # exactly ONE pattern line, like the Python reader: a second line would make the two disagree
  [ "$(printf '%s\n' "$patterns" | grep -c .)" = 1 ] && printf '%s\n' "$patterns"
}

# _mdt_missing_declared_keys <config> <example> <required|optional>: prints the
# keys the example declares that the config has no line for, restricted to the
# render-time ones (required) or the runtime-optional ones (optional).
_mdt_missing_declared_keys() {
  local conf="$1" example="$2" mode="$3" key re
  re=$(_mdt_runtime_optional_re)
  [ -n "$re" ] || { echo "runtime-optional-keys.re is missing or empty" >&2; return 1; }
  while IFS= read -r key; do
    grep -qE "^${key}=" "$conf" 2>/dev/null && continue
    if [[ "$key" =~ $re ]]; then
      [ "$mode" = optional ] && echo "$key"
    else
      [ "$mode" = required ] && echo "$key"
    fi
  done < <(grep -oE '^[A-Z_][A-Z0-9_]*=' "$example" | sed 's/=$//')
  return 0
}

# _mdt_io_pin_notice: one loud NOTICE when no DEV_IO_*_MAX pin is set, naming the
# effective dev.slice cap source (call after _mdt_resolve_static_io).
_mdt_io_pin_notice() {
  [ -z "${DEV_IO_RBPS_MAX:-}${DEV_IO_WBPS_MAX:-}${DEV_IO_RIOPS_MAX:-}${DEV_IO_WIOPS_MAX:-}" ] || return 0
  echo "NOTICE: no DEV_IO_{RBPS,WBPS,RIOPS,WIOPS}_MAX pin is set. Effective dev.slice cap source: the static fallback DEV_STATIC_* = ${MDT_STATIC_RBPS}/${MDT_STATIC_WBPS} bytes/s r/w, ${MDT_STATIC_RIOPS}/${MDT_STATIC_WIOPS} IOPS r/w (lowered only by a current, plausible baseline). On a virtual disk pin the caps from a sustained measurement (docs/CONSUMERS.md) or run install.sh --wizard."
}

# _mdt_apply_damon_policy: DAMON_STAT_DISABLE_DEFAULT=yes (the default since 2026-10-20)
# installs MDT's boot-time tmpfiles rule and applies it now when the kernel exposes the
# parameter; anything else removes MDT's override.  DAMON_STAT is a host-wide consumer
# that excludes cgprofile's generic DAMON sysfs sessions.  Needs HERE (the host-setup
# directory).  Test seams: DAMON_STAT_TMPFILES, MDT_DAMON_PARAM.  rc 2 = could not apply.
_mdt_apply_damon_policy() {
  local DAMON_STAT_TMPFILES="${DAMON_STAT_TMPFILES:-/etc/tmpfiles.d/mdt-damon-stat.conf}"
  local param="${MDT_DAMON_PARAM:-/sys/module/damon_stat/parameters/enabled}"
  if [ "${DAMON_STAT_DISABLE_DEFAULT:-}" = yes ]; then
    install -m 0644 "$HERE/units/mdt-damon-stat.conf" "$DAMON_STAT_TMPFILES"
    if [ -e "$param" ]; then
      systemd-tmpfiles --create "$DAMON_STAT_TMPFILES" \
        || { echo "ERROR: selected DAMON_STAT disable policy could not be applied" >&2; return 2; }
      echo "DAMON_STAT disabled for this boot and by the boot-time tmpfiles policy"
    else
      echo "DAMON_STAT parameter is not exposed by this kernel; the persistent rule remains installed"
    fi
  elif [ -f "$DAMON_STAT_TMPFILES" ]; then
    rm -f -- "$DAMON_STAT_TMPFILES"
    echo "Removed MDT's DAMON_STAT boot override; the current kernel value is unchanged until reboot"
  fi
  return 0
}

# --- writeback and root-queue settings (MDT-HS-05) -------------------------------------------
# Keys (host-setup.env): IO_WRITEBACK_DIRTY_BYTES, IO_WRITEBACK_DIRTY_BACKGROUND_BYTES,
# IO_ROOT_QUEUE_NR_REQUESTS, IO_ROOT_QUEUE_MAX_SECTORS_KB.  Grammar (validated ONCE, by the Python
# validator in mdt-host-setup-wizard.py that both the wizard and install.sh --validate-config run;
# the disk-dependent upper bound max_sectors_kb <= max_hw_sectors_kb through the same module):
# a positive integer, or `kernel` / empty = leave the kernel's own value untouched, render nothing.
# Why and how measured: scripts/gstammtisch-guide/HOST-IO-INTERFERENCE.md sections 4.2-4.5 and 5.
MDT_IO_WB_SYSCTL_FILE_NAME=90-mdt-io-writeback.conf
MDT_IO_WB_UDEV_FILE_NAME=61-mdt-io-queue.rules
MDT_IO_WB_INTERIM_SYSCTL_NAME=90-mdt-io-writeback-interim.conf
MDT_IO_WB_INTERIM_UDEV_NAME=61-mdt-io-queue-interim.rules

# _mdt_io_wb_value <KEY>: the numeric value, or nothing for `kernel`, empty or unset.
_mdt_io_wb_value() {
  local v="${!1:-}"
  case "$v" in kernel|"") ;; *) printf '%s\n' "$v" ;; esac
}

# _mdt_io_queue_keys_set: success when either queue key carries a number.
_mdt_io_queue_keys_set() {
  [ -n "$(_mdt_io_wb_value IO_ROOT_QUEUE_NR_REQUESTS)$(_mdt_io_wb_value IO_ROOT_QUEUE_MAX_SECTORS_KB)" ]
}

# _mdt_root_disk_node: `/dev/<DEVNAME>` of the WHOLE disk under `/`, derived (never typed) with the
# MDT-HS-04 helpers; the udev rule matches KERNEL==<DEVNAME> of exactly this disk.  Test seam:
# MDT_ROOT_TARGET (default /).  rc 1 with the reason on stderr.
_mdt_root_disk_node() {
  local src
  src=$(findmnt -no SOURCE --target "${MDT_ROOT_TARGET:-/}" 2>/dev/null) || {
    echo "findmnt could not name the filesystem source of ${MDT_ROOT_TARGET:-/}" >&2; return 1; }
  src=${src%%\[*}                      # btrfs: /dev/vda2[/@] -> /dev/vda2
  case "$src" in
    /dev/?*) ;;
    *) echo "the source of ${MDT_ROOT_TARGET:-/} is '$src', not a /dev block node" >&2; return 1 ;;
  esac
  _mdt_io_systemd_dev "$src"
}

# _mdt_io_queue_attr <devname> <attr>: the live value of /sys/block/<devname>/queue/<attr>.
_mdt_io_queue_attr() {
  local f="${MDT_SYSFS_ROOT:-/sys}/block/$1/queue/$2"
  [ -r "$f" ] || { echo "cannot read $f" >&2; return 1; }
  tr -d '[:space:]' < "$f"
}

# _mdt_render_writeback_sysctl: the sysctl.d file body, ONLY the keys that are set; rc 1 (no output)
# when none is.
_mdt_render_writeback_sysctl() {
  local d b
  d=$(_mdt_io_wb_value IO_WRITEBACK_DIRTY_BYTES)
  b=$(_mdt_io_wb_value IO_WRITEBACK_DIRTY_BACKGROUND_BYTES)
  [ -n "$d$b" ] || return 1
  cat <<EOF
# Managed by MDT host-setup (install.sh): do not edit; change IO_WRITEBACK_* in /etc/mdt/host-setup.env
# and re-run install.sh. Only keys that are set appear here; \`kernel\` renders no line.
# Why and how measured: scripts/gstammtisch-guide/HOST-IO-INTERFERENCE.md sections 4.2-4.5 and 5
# (host-global: these also bound every production container's writeback, section 4.5).
EOF
  [ -z "$b" ] || echo "vm.dirty_background_bytes = $b"
  [ -z "$d" ] || echo "vm.dirty_bytes = $d"
}

# _mdt_render_queue_udev <devname>: the udev rule body for that whole disk, ONLY the attributes that are
# set; rc 1 (no output) when none is.
_mdt_render_queue_udev() {
  local dev="$1" n m attrs=""
  n=$(_mdt_io_wb_value IO_ROOT_QUEUE_NR_REQUESTS)
  m=$(_mdt_io_wb_value IO_ROOT_QUEUE_MAX_SECTORS_KB)
  [ -n "$n$m" ] || return 1
  [ -z "$n" ] || attrs="$attrs, ATTR{queue/nr_requests}=\"$n\""
  [ -z "$m" ] || attrs="$attrs, ATTR{queue/max_sectors_kb}=\"$m\""
  cat <<EOF
# Managed by MDT host-setup (install.sh): do not edit; change IO_ROOT_QUEUE_* in /etc/mdt/host-setup.env
# and re-run install.sh. The disk (KERNEL==$dev) was DERIVED as the whole disk under /, never typed.
# Sorts AFTER 60-mdt-bfq-scheduler.rules ON PURPOSE: a scheduler change resets nr_requests, so the
# scheduler rule must run first. Why and how measured: scripts/gstammtisch-guide/HOST-IO-INTERFERENCE.md
# sections 4.3-4.5 and 5.
ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="$dev"$attrs
EOF
}

# _mdt_io_wb_check_one <label> <wanted> <live> <where> <remedy>: one comparison.  An empty <live> is
# "could not read" (never certified), an empty <wanted> is "not managed" (nothing to compare).
_mdt_io_wb_check_one() {
  [ -n "$2" ] || return 0
  if [ -z "$3" ]; then
    echo "ERROR: could not read the live $1 ($4) — cannot verify the intended $2. Remedy: $5" >&2
    return 1
  fi
  if [ "$3" != "$2" ]; then
    echo "ERROR: live $1 is $3 but the intended value is $2 ($4). Remedy: $5" >&2
    return 1
  fi
  return 0
}

_mdt_io_cat() { tr -d '[:space:]' < "$1" 2>/dev/null; }

# _mdt_io_wb_readback <devname-or-empty> <sysctl-file> <udev-file>: compare the LIVE values with the
# intended ones; every mismatch or unreadable value is named with its file and remedy.  rc 1 on any.
_mdt_io_wb_readback() {
  local dev="$1" sfile="$2" ufile="$3" bad=0 proc="${MDT_PROC_ROOT:-/proc}" live
  local sremedy="a later file in /etc/sysctl.d or /run/sysctl.d may override $sfile (check: sysctl vm.dirty_bytes vm.dirty_background_bytes), fix it and re-run install.sh"
  local uremedy="check that no later udev rule overrides $ufile (ls /etc/udev/rules.d), that KERNEL==$dev matches, then udevadm trigger --action=change --sysname-match=$dev and re-run install.sh"
  live=$(_mdt_io_cat "$proc/sys/vm/dirty_bytes")
  _mdt_io_wb_check_one vm.dirty_bytes "$(_mdt_io_wb_value IO_WRITEBACK_DIRTY_BYTES)" "$live" "$proc/sys/vm/dirty_bytes, written by $sfile" "$sremedy" || bad=1
  live=$(_mdt_io_cat "$proc/sys/vm/dirty_background_bytes")
  _mdt_io_wb_check_one vm.dirty_background_bytes "$(_mdt_io_wb_value IO_WRITEBACK_DIRTY_BACKGROUND_BYTES)" "$live" "$proc/sys/vm/dirty_background_bytes, written by $sfile" "$sremedy" || bad=1
  if [ -n "$dev" ]; then
    live=$(_mdt_io_queue_attr "$dev" nr_requests 2>/dev/null) || live=""
    _mdt_io_wb_check_one "/sys/block/$dev/queue/nr_requests" "$(_mdt_io_wb_value IO_ROOT_QUEUE_NR_REQUESTS)" "$live" "written by $ufile" "$uremedy" || bad=1
    live=$(_mdt_io_queue_attr "$dev" max_sectors_kb 2>/dev/null) || live=""
    _mdt_io_wb_check_one "/sys/block/$dev/queue/max_sectors_kb" "$(_mdt_io_wb_value IO_ROOT_QUEUE_MAX_SECTORS_KB)" "$live" "written by $ufile" "$uremedy" || bad=1
  fi
  return "$bad"
}

# _mdt_io_interim_keys <sysctl|udev> <file>: the keys an interim file SETS, one per line (parsed, never
# assumed): sysctl `vm.x`, or udev `queue/x` from ATTR{queue/x}.  Content that is not a recognised
# assignment prints `(unparsed)` once so it can never be mistaken for "sets nothing".
_mdt_io_interim_keys() {
  local kind="$1" file="$2" meaningful parsed
  meaningful=$(grep -cvE '^[[:space:]]*(#|;|$)' "$file" 2>/dev/null || true)
  if [ "$kind" = sysctl ]; then
    parsed=$(grep -vE '^[[:space:]]*(#|;)' "$file" 2>/dev/null | sed -n 's/^[[:space:]]*\([A-Za-z0-9_.]*\)[[:space:]]*=.*/\1/p')
  else
    parsed=$(grep -vE '^[[:space:]]*#' "$file" 2>/dev/null | grep -o 'ATTR{queue/[A-Za-z0-9_]*}' | sed 's/^ATTR{//; s/}$//')
  fi
  [ -z "$parsed" ] || printf '%s\n' "$parsed"
  if [ "${meaningful:-0}" -gt 0 ] && [ -z "$parsed" ]; then echo "(unparsed)"; fi
}

# _mdt_io_interim_missing <sysctl|udev> <interim-file> <own-file>: the interim keys MDT's own file does not
# carry, each prefixed by a space (empty output = every key is carried).  An absent own file carries nothing.
_mdt_io_interim_missing() {
  local kind="$1" interim="$2" own="$3" key out=""
  while IFS= read -r key; do
    [ -n "$key" ] || continue
    if [ "$key" = "(unparsed)" ] || [ ! -f "$own" ]; then out="$out $key"; continue; fi
    if [ "$kind" = sysctl ]; then
      grep -qE "^[[:space:]]*${key//./\\.}[[:space:]]*=" "$own" || out="$out $key"
    else
      grep -qF "ATTR{$key}" "$own" || out="$out $key"
    fi
  done < <(_mdt_io_interim_keys "$kind" "$interim")
  printf '%s' "$out"
}

# _mdt_io_interim_env_keys "<space separated keys>": the host-setup.env keys that would carry them.
_mdt_io_interim_env_keys() {
  local key out=""
  for key in $1; do
    case "$key" in
      vm.dirty_bytes) out="$out IO_WRITEBACK_DIRTY_BYTES" ;;
      vm.dirty_background_bytes) out="$out IO_WRITEBACK_DIRTY_BACKGROUND_BYTES" ;;
      queue/nr_requests) out="$out IO_ROOT_QUEUE_NR_REQUESTS" ;;
      queue/max_sectors_kb) out="$out IO_ROOT_QUEUE_MAX_SECTORS_KB" ;;
      *) out="$out (no MDT key for $key)" ;;
    esac
  done
  printf '%s' "${out# }"
}

# _mdt_io_disk_identity <devname>: `key=value` lines of the disk identity fields IOB-02 defines
# (model, serial, size_bytes, wwn), read from sysfs; fields sysfs does not expose are omitted, never
# invented.  virtio-blk exposes `serial`; SCSI/NVMe expose `device/model|serial|wwid`.
_mdt_io_disk_identity() {
  local base="${MDT_SYSFS_ROOT:-/sys}/block/$1" f v sectors
  for f in model serial wwid; do
    v=""
    for src in "$base/device/$f" "$base/$f"; do
      [ -r "$src" ] && { v=$(tr -s '[:space:]' ' ' < "$src" | sed 's/^ //; s/ $//'); [ -z "$v" ] || break; }
    done
    [ -z "$v" ] || printf '%s=%s\n' "$([ "$f" = wwid ] && echo wwn || echo "$f")" "$v"
  done
  if sectors=$(tr -d '[:space:]' < "$base/size" 2>/dev/null) && [ -n "$sectors" ]; then
    printf 'size_bytes=%s\n' "$((sectors * 512))"
  fi
}

# _mdt_apply_writeback_settings: render, apply, READ BACK, and only then retire the interim files.
# Test seams: MDT_SYSCTL_D, MDT_UDEV_RULES_D (defaults /etc/sysctl.d, /etc/udev/rules.d), MDT_PROC_ROOT,
# MDT_SYSFS_ROOT, MDT_ROOT_TARGET, and PATH shims for sysctl/udevadm.  rc 0 ok, 2 refused/failed.
# A key changed back to `kernel` loses MDT's line but the runtime value cannot be restored here
# (the kernel's boot value is unknown at runtime; never guessed): a NOTICE says so.
_mdt_apply_writeback_settings() {
  local sd="${MDT_SYSCTL_D:-/etc/sysctl.d}" ud="${MDT_UDEV_RULES_D:-/etc/udev/rules.d}"
  local sfile="$sd/$MDT_IO_WB_SYSCTL_FILE_NAME" ufile="$ud/$MDT_IO_WB_UDEV_FILE_NAME"
  local isfile="$sd/$MDT_IO_WB_INTERIM_SYSCTL_NAME" iufile="$ud/$MDT_IO_WB_INTERIM_UDEV_NAME"
  local dev="" node body k prev_dropped=""
  if _mdt_io_queue_keys_set; then
    if ! node=$(_mdt_root_disk_node 2>&1); then
      echo "ERROR: IO_ROOT_QUEUE_* is set but the whole disk under / cannot be derived ($node); nothing was applied. Remedy: run on the host, or set both queue keys to kernel." >&2
      return 2
    fi
    dev=${node#/dev/}
  fi
  # which keys did MDT's (or the interim) files hold before? (for the "back to kernel" NOTICE)
  for k in vm.dirty_bytes vm.dirty_background_bytes; do
    if grep -qs "^${k}[[:space:]]*=" "$sfile" "$isfile"; then
      case "$k" in
        vm.dirty_bytes) [ -n "$(_mdt_io_wb_value IO_WRITEBACK_DIRTY_BYTES)" ] || prev_dropped="$prev_dropped $k" ;;
        *) [ -n "$(_mdt_io_wb_value IO_WRITEBACK_DIRTY_BACKGROUND_BYTES)" ] || prev_dropped="$prev_dropped $k" ;;
      esac
    fi
  done
  for k in nr_requests max_sectors_kb; do
    if grep -qs "queue/$k" "$ufile" "$iufile"; then
      case "$k" in
        nr_requests) [ -n "$(_mdt_io_wb_value IO_ROOT_QUEUE_NR_REQUESTS)" ] || prev_dropped="$prev_dropped $k" ;;
        *) [ -n "$(_mdt_io_wb_value IO_ROOT_QUEUE_MAX_SECTORS_KB)" ] || prev_dropped="$prev_dropped $k" ;;
      esac
    fi
  done
  mkdir -p "$sd" "$ud" || return 2
  if body=$(_mdt_render_writeback_sysctl); then
    printf '%s\n' "$body" > "$sfile.tmp" && mv -f "$sfile.tmp" "$sfile" || return 2
    echo "rendered $sfile"
  elif [ -f "$sfile" ]; then
    rm -f -- "$sfile"; echo "removed $sfile (no writeback key is set)"
  fi
  if [ -n "$dev" ] && body=$(_mdt_render_queue_udev "$dev"); then
    printf '%s\n' "$body" > "$ufile.tmp" && mv -f "$ufile.tmp" "$ufile" || return 2
    echo "rendered $ufile (disk $dev, derived)"
  elif [ -f "$ufile" ]; then
    rm -f -- "$ufile"; echo "removed $ufile (no queue key is set)"
    udevadm control --reload-rules >/dev/null 2>&1 || true   # the removed rule must stop applying
  fi
  if [ -f "$sfile" ]; then
    sysctl -p "$sfile" >/dev/null 2>&1 || {
      echo "ERROR: sysctl -p $sfile failed; the interim files were left in place. Remedy: run 'sysctl -p $sfile' by hand and read its error." >&2
      return 2; }
  fi
  if [ -n "$dev" ]; then
    { udevadm control --reload-rules && udevadm trigger --action=change --sysname-match="$dev" && udevadm settle; } >/dev/null 2>&1 || {
      echo "ERROR: udev reload/trigger/settle failed for $dev; the interim files were left in place. Remedy: udevadm control --reload-rules && udevadm trigger --action=change --sysname-match=$dev" >&2
      return 2; }
  fi
  if ! _mdt_io_wb_readback "$dev" "$sfile" "$ufile"; then
    echo "ERROR: the live writeback/queue values do not match host-setup.env after install; the interim files were left in place (still holding the previous values)." >&2
    return 2
  fi
  # Verified: retire an interim file ONLY when MDT's own file carries EVERY key the interim file sets
  # (parsed from the interim file, never assumed) -- all of them verified live above.  Otherwise the
  # interim file is the only thing still persisting a value, so it stays, with a WARN (MDT-HS-05 H-46).
  local missing
  if [ -f "$isfile" ]; then
    missing=$(_mdt_io_interim_missing sysctl "$isfile" "$sfile")
    if [ -z "$missing" ]; then
      rm -f -- "$isfile"; echo "NOTICE: removed the interim file $isfile; $sfile replaces it"
    else
      echo "WARN: kept the interim file $isfile: it sets${missing} which $sfile does not carry, so it is still the only thing persisting them across a reboot. Remedy: run install.sh --wizard and set $(_mdt_io_interim_env_keys "$missing"), then re-run install.sh (or delete the interim file by hand if you no longer want those values)." >&2
    fi
  fi
  if [ -f "$iufile" ]; then
    missing=$(_mdt_io_interim_missing udev "$iufile" "$ufile")
    if [ -z "$missing" ]; then
      rm -f -- "$iufile"; echo "NOTICE: removed the interim file $iufile; $ufile replaces it"
      udevadm control --reload-rules >/dev/null 2>&1 || true
    else
      echo "WARN: kept the interim file $iufile: it sets${missing} which $ufile does not carry, so it is still the only thing persisting them across a reboot. Remedy: run install.sh --wizard and set $(_mdt_io_interim_env_keys "$missing"), then re-run install.sh (or delete the interim file by hand if you no longer want those values)." >&2
    fi
  fi
  if [ -n "$prev_dropped" ]; then
    echo "NOTICE: now set to kernel, MDT no longer renders:$prev_dropped. The running value is unchanged: MDT cannot restore the kernel's boot value at runtime (it does not know it). Reboot, or set the value by hand, to return to the kernel default." >&2
  fi
  return 0
}


# --- guard (terminate) policy confirmation, recorded durably (MDT-HS-02) -------------------
# The wizard's second confirmation of BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate is recorded
# in a root-owned file under /var/lib/mdt: the confirmed settings, a timestamp and a digest of
#   BUILDX_ACCIDENTAL_CONTAINER_POLICY, DEV_BUILDKITD_IMAGE (the identity the guard approves
#   by) and this host's machine-id.
# It is an AUDIT TRAIL bound to what was confirmed and where, not an access control (root can
# write it). A non-wizard re-install with an unchanged, already-confirmed terminate setting on
# the same host proceeds and keeps the running guard; a changed policy, image or host does not
# match the record and refuses BEFORE anything is touched. mdt-buildkit-guard.py verifies the
# same digest before it acts in terminate mode and falls back to report-only, loudly.
# The algorithm is shared with scripts/mdt-buildkit-guard.py (guard_digest): keep them equal.
_mdt_guard_digest() { # <policy> <image>
  local mid
  mid=$({ tr -d '[:space:]' < "${MDT_MACHINE_ID_FILE:-/etc/machine-id}"; } 2>/dev/null || true)
  printf 'BUILDX_ACCIDENTAL_CONTAINER_POLICY=%s\nDEV_BUILDKITD_IMAGE=%s\nmachine-id=%s\n' \
    "$1" "$2" "${mid:-none}" | sha256sum | cut -d' ' -f1
}

_mdt_guard_record_write() { # <record-file> <policy> <image>
  local record="$1" policy="$2" image="$3"
  mkdir -p "$(dirname "$record")" || return 1
  printf 'policy=%s\nimage=%s\nconfirmed_at=%s\nsha256=%s\n' "$policy" "$image" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(_mdt_guard_digest "$policy" "$image")" \
    > "$record.tmp" && mv -f "$record.tmp" "$record"
}

_mdt_guard_confirmed() { # <record-file> <policy> <image>
  [ -f "$1" ] && grep -qx "policy=$2" "$1" && grep -qx "sha256=$(_mdt_guard_digest "$2" "$3")" "$1"
}

# _mdt_guard_decision <policy> <wizard 0|1> <record-file> <image> -> record | keep | clear | refuse | invalid
_mdt_guard_decision() {
  case "$1" in
    terminate)
      if [ "$2" = 1 ]; then echo record
      elif _mdt_guard_confirmed "$3" terminate "${4:-}"; then echo keep
      else echo refuse
      fi ;;
    report-only) echo clear ;;
    *) echo invalid ;;
  esac
}

# _mdt_apply_damon_step <required 0|1>: the optional DAMON_STAT write must not abort an install
# halfway (units and config are already replaced, services not yet activated). A failure is a
# loud WARN and the install continues, unless the operator required it (--require-damon-disable).
# The boot-time rule is already installed when the immediate write fails.
_mdt_apply_damon_step() {
  local required="${1:-0}"
  if _mdt_apply_damon_policy; then return 0; fi
  if [ "$required" = 1 ]; then
    echo "ERROR: DAMON_STAT disable was required (--require-damon-disable) and could not be applied" >&2
    return 2
  fi
  echo "WARN: ********************************************************************" >&2
  echo "WARN: DAMON_STAT could NOT be disabled now; the install continues. The boot-time rule is installed;" >&2
  echo "WARN: the cgroup-profiler daemon cannot use the DAMON sysfs interface until DAMON_STAT is off" >&2
  echo "WARN: (reboot, or: echo N > /sys/module/damon_stat/parameters/enabled). Re-run with --require-damon-disable to make this fatal." >&2
  echo "WARN: ********************************************************************" >&2
  return 0
}
