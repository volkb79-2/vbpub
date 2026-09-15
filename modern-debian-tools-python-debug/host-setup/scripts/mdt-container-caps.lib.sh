#!/usr/bin/env bash
# mdt host-setup — shared per-container IO-cap logic.
#
# Sourced by BOTH mdt-apply-dev-caps.sh (the periodic sweep, a backstop —
# see host-setup/README.md "Persistence model") and mdt-io-cap-watcher.sh (the
# instant docker-events watcher). One definition of "how a container gets
# capped" instead of two copies that can drift apart.
#
# Callers must define `log()` before sourcing this, and must call
# `_mdt_load_config` then `_mdt_derive_sweep_caps` before `_mdt_apply_container_caps`.
# Not meant to be executed directly.

# _mdt_load_config: source $CONF, apply defaults.
_mdt_load_config() {
  # shellcheck disable=SC1090
  [ -f "$CONF" ] && . "$CONF" || log "WARN: $CONF not found — using built-in defaults"

  SWEEP_IO_CAP_PCT="${SWEEP_IO_CAP_PCT:-80}"
  TESTRUNNER_IMAGE_PATTERNS="${TESTRUNNER_IMAGE_PATTERNS:-*test-runner*}"
  BUILDKIT_NAME_PATTERNS="${BUILDKIT_NAME_PATTERNS:-buildx_buildkit_*}"
  DEVCONTAINER_NAME_PATTERNS="${DEVCONTAINER_NAME_PATTERNS:-*devcontainer*}"
  IO_BASELINE_ENV="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"
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
# provenance warnings mdt-apply-dev-caps.sh always has (burst-v1 reads high on
# a VM; unrecognised/missing method means "unverified").
_mdt_load_baseline() {
  RIOPS_MAX="" WIOPS_MAX="" RBW_MAX_BPS="" WBW_MAX_BPS="" MEASURE_METHOD="" MEASURED_AT=""
  [ -f "$IO_BASELINE_ENV" ] || { log "no $IO_BASELINE_ENV — per-container caps use the static fallback (run mdt-io-baseline.py)"; return; }
  local _mtime _now _age _invalid=""
  _mtime=$(stat -c %Y "$IO_BASELINE_ENV" 2>/dev/null) || _invalid="mtime unreadable"
  _now=$(date +%s)
  if [ -z "$_invalid" ] && { [ $(( _now - _mtime )) -gt $((30 * 86400)) ] || [ "$_mtime" -gt "$_now" ]; }; then
    _invalid="stale (older than 30 days or timestamp is in the future)"
  fi
  # shellcheck disable=SC1090
  . "$IO_BASELINE_ENV" 2>/dev/null || true
  [ "${MEASURE_METHOD:-}" = sustained-v3 ] || _invalid="${_invalid:-method is not sustained-v3}"
  [ -n "${MEASURED_AT:-}" ] || _invalid="${_invalid:-MEASURED_AT is missing}"
  for _baseline_key in RIOPS_MAX WIOPS_MAX RBW_MAX_BPS WBW_MAX_BPS; do
    _baseline_value="${!_baseline_key:-}"
    case "$_baseline_value" in
      ''|*[!0-9]*) _invalid="${_invalid:-$_baseline_key is not a positive integer}" ;;
      0) _invalid="${_invalid:-$_baseline_key is zero}" ;;
    esac
  done
  if [ -n "$_invalid" ]; then
    log "WARN: ignoring $IO_BASELINE_ENV — $_invalid; per-container and runtime estate caps use static fallbacks"
    RIOPS_MAX="" WIOPS_MAX="" RBW_MAX_BPS="" WBW_MAX_BPS="" MEASURE_METHOD=""
  fi
}

# _mdt_derive_sweep_caps: sets SWEEP_RBPS/WBPS/RIOPS/WIOPS/SWEEP_SRC (and
# SWEEP_SKIP=1 if the derived cap is unusable). Call after _mdt_load_config,
# _mdt_load_baseline and _mdt_discover_io_dev_path.
_mdt_derive_sweep_caps() {
  SWEEP_RBPS="${SWEEP_RBPS:-31457280}"; SWEEP_WBPS="${SWEEP_WBPS:-31457280}"
  SWEEP_RIOPS="${SWEEP_RIOPS:-200}";    SWEEP_WIOPS="${SWEEP_WIOPS:-400}"
  SWEEP_SRC="static fallback — no baseline, run mdt-io-baseline.py"
  SWEEP_SKIP=0
  # All four or none: a partial baseline would derive a 0 cap, and 0 in io.max
  # is not "unlimited", it stops the container's IO dead.
  if [ -n "${RIOPS_MAX:-}" ] && [ -n "${WIOPS_MAX:-}" ] \
     && [ -n "${RBW_MAX_BPS:-}" ] && [ -n "${WBW_MAX_BPS:-}" ]; then
    SWEEP_RIOPS=$(( RIOPS_MAX   * SWEEP_IO_CAP_PCT / 100 ))
    SWEEP_WIOPS=$(( WIOPS_MAX   * SWEEP_IO_CAP_PCT / 100 ))
    SWEEP_RBPS=$((  RBW_MAX_BPS * SWEEP_IO_CAP_PCT / 100 ))
    SWEEP_WBPS=$((  WBW_MAX_BPS * SWEEP_IO_CAP_PCT / 100 ))
    SWEEP_SRC="${SWEEP_IO_CAP_PCT}% of baseline"
  fi
  if [ "$SWEEP_RIOPS" -lt 1 ] || [ "$SWEEP_WIOPS" -lt 1 ] \
     || [ "$SWEEP_RBPS" -lt 1 ] || [ "$SWEEP_WBPS" -lt 1 ]; then
    log "WARN: derived per-container cap <= 0 (bad baseline?) — skipping container caps"
    SWEEP_SKIP=1
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
  local cid="$1" label="$2" deprio="${3:-0}" pid scope scope_rel unit props=()
  pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null) || return 0
  { [ -z "$pid" ] || [ "$pid" = "0" ]; } && return 0
  [ -n "${IO_DEV_PATH:-}" ] || { log "WARN: $label ($cid): no io device — skipped"; return 0; }
  scope_rel=$(awk -F: '/^0::/{print $3; exit}' "/proc/$pid/cgroup" 2>/dev/null)
  if [ -z "$scope_rel" ] || [ "$scope_rel" = / ]; then
    log "WARN: $label ($cid): no usable unified cgroup path in /proc/$pid/cgroup — skipped; a later sweep will retry"
    return 0
  fi
  scope="$CG$scope_rel"
  # buildkitd nests sub-cgroups INSIDE its container; trim to the scope
  # component — limits on the scope cover the whole subtree.
  case "$scope" in *".scope/"*) scope="${scope%%.scope/*}.scope" ;; esac
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
  props+=("IOReadBandwidthMax=$IO_DEV_PATH $SWEEP_RBPS" "IOWriteBandwidthMax=$IO_DEV_PATH $SWEEP_WBPS"
          "IOReadIOPSMax=$IO_DEV_PATH $SWEEP_RIOPS"     "IOWriteIOPSMax=$IO_DEV_PATH $SWEEP_WIOPS")
  # set-property (not a raw io.max write): systemd re-applies its own recorded
  # properties to a scope on every daemon-reload, silently wiping raw writes.
  if systemctl set-property --runtime "$unit" "${props[@]}" 2>/dev/null; then
    log "$label ($cid): io.max=${SWEEP_RIOPS}r/${SWEEP_WIOPS}w IOPS $((SWEEP_RBPS/1048576))/$((SWEEP_WBPS/1048576))MB/s r/w (${SWEEP_SRC})$([ "$deprio" = 1 ] && echo ', io.weight=1')"
  else
    log "WARN: $label ($cid): set-property failed — skipped"
    return 0
  fi
  # BFQ per-scope weight has no systemd property — raw write, and because
  # systemd does not manage this attribute the write survives daemon-reload.
  [ "$deprio" = 1 ] && { echo "default 1" > "$scope/io.bfq.weight" 2>/dev/null || true; }
  return 0
}

# _mdt_classify_and_apply <container-id> — inspects the container's image and
# name, matches it against the three categories, and applies caps if matched.
# Shared by the sweep's docker-ps loop and the watcher's docker-events loop.
_mdt_classify_and_apply() {
  local cid="$1" img name
  [ "${SWEEP_SKIP:-0}" = 1 ] && return 0
  img=$(docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || true)
  name=$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | tr -d '/' || true)
  if _mdt_match "$img" "$TESTRUNNER_IMAGE_PATTERNS"; then
    _mdt_apply_container_caps "$cid" "bench:$name" 1
  elif _mdt_match "$name" "$BUILDKIT_NAME_PATTERNS"; then
    _mdt_apply_container_caps "$cid" "buildkit:$name" 1
  elif _mdt_match "$name" "$DEVCONTAINER_NAME_PATTERNS"; then
    _mdt_apply_container_caps "$cid" "devcontainer:$name" 0
  fi
}
