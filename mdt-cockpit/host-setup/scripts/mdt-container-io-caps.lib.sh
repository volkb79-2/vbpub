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
  local cid="$1" label="$2" deprio="${3:-0}" scope unit props=()
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
  [ "$deprio" = 1 ] && props+=(IOWeight=1)
  props+=("IOReadBandwidthMax=$IO_DEV_PATH $WATCHER_RBPS" "IOWriteBandwidthMax=$IO_DEV_PATH $WATCHER_WBPS"
          "IOReadIOPSMax=$IO_DEV_PATH $WATCHER_RIOPS"     "IOWriteIOPSMax=$IO_DEV_PATH $WATCHER_WIOPS")
  # set-property (not a raw io.max write): systemd re-applies its own recorded
  # properties to a scope on every daemon-reload, silently wiping raw writes.
  if systemctl set-property --runtime "$unit" "${props[@]}" 2>/dev/null; then
    log "$label ($cid): io.max=${WATCHER_RIOPS}r/${WATCHER_WIOPS}w IOPS $((WATCHER_RBPS/1048576))/$((WATCHER_WBPS/1048576))MB/s r/w (${WATCHER_SRC})$([ "$deprio" = 1 ] && echo ', io.weight=1')"
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
  num=$((10#${BASH_REMATCH[1]}))
  case "${BASH_REMATCH[2]}" in
    K) mult=1024 ;; M) mult=$((1024**2)) ;; G) mult=$((1024**3)) ;; T) mult=$((1024**4)) ;;
  esac
  [ "$num" -ge 1 ] || return 1
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
# CONTENT; `[ -s file ]` on a cgroup control file is always false.
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
  if [ ! -d "$dir" ]; then
    log "$label: cgroup not active yet — properties recorded but NOT verified (no process is governed until the slice starts; the next run verifies)"
    return 2
  fi
  if ! got=$(_mdt_io_max_fields "$dir" "$majmin"); then
    log "ERROR: $label: kernel io.max has NO entry for $majmin (content: '$(tr '\n' ';' < "$dir/io.max" 2>/dev/null)') — the intended cap is NOT in effect"
    return 1
  fi
  set -- $got
  if { [ -z "$want_rb" ] || [ "$1" = "$want_rb" ]; } && { [ -z "$want_wb" ] || [ "$2" = "$want_wb" ]; } \
     && { [ -z "$want_ri" ] || [ "$3" = "$want_ri" ]; } && { [ -z "$want_wi" ] || [ "$4" = "$want_wi" ]; }; then
    log "$label: verified kernel io.max $majmin rbps=$1 wbps=$2 riops=$3 wiops=$4"
    return 0
  fi
  log "ERROR: $label: kernel io.max $majmin is rbps=$1 wbps=$2 riops=$3 wiops=$4 but intended rbps=${want_rb:--} wbps=${want_wb:--} riops=${want_ri:--} wiops=${want_wi:--} — the intended cap is NOT in effect"
  return 1
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
      case "$devpath" in /dev/?*) ;; *) printf '%s unresolved\n' "$dev"; continue ;; esac
    fi
    if ! mm=$(_mdt_dev_majmin "$devpath"); then
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
