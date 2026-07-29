#!/usr/bin/env bash
# Manage the Soulmask unified read-only tmpfs ramdisk.
#
# The ramdisk only takes effect at container start — the game mmap's files
# at startup and the mmap stays bound to the original inode for its
# lifetime. This script sets up or tears down the host-side mounts; you must
# then do a clean stop + start of the Soulmask instances via Wings for the
# change to apply.
#
# The tmpfs is read-only during normal operation. Game updates MUST go
# through the manual procedure:
#   stop all containers → --stop → start main server → update
#   → stop main server → --start → start all containers
#
# Usage:
#   soulmask_tmpfs-toggle.sh              # show status + validate
#   soulmask_tmpfs-toggle.sh --start      # activate (idempotent)
#   soulmask_tmpfs-toggle.sh --stop       # deactivate (refuses if containers run)
#   soulmask_tmpfs-toggle.sh --help       # show this message
set -euo pipefail

SERVICE="soulmask_tmpfs.service"
STATE_FILE="/run/soulmask_tmpfs.state"
RAMDISK="/mnt/soulmask_tmpfs"
INSTANCES_DIR="${GSTAMMTISCH_ETC:-/etc/gstammtisch}/instances.d"

log()   { echo "[soulmask_tmpfs] $*"; }
warn()  { echo "[soulmask_tmpfs] WARNING: $*"; }
err()   { echo "[soulmask_tmpfs] ERROR: $*" >&2; }

# ── helpers ───────────────────────────────────────────────────────────────────

_is_active() {
  systemctl is-active --quiet "$SERVICE" 2>/dev/null
}

_running_container_ids() {
  # Print container IDs of running WSServer-Linux-Shipping containers.
  for c in $(docker ps -q 2>/dev/null); do
    [ "$(docker inspect -f '{{.HostConfig.PidMode}}' "$c" 2>/dev/null)" = "host" ] && continue
    docker top "$c" 2>/dev/null | grep -q 'WSServer-Linux-Shipping' && echo "$c" || true
  done
}

_running_container_count() {
  _running_container_ids | wc -l
}

# ── status ────────────────────────────────────────────────────────────────────

_show_status() {
  echo ""
  echo "══════════════════════════════════════════════════════════════"
  echo "  Soulmask unified tmpfs ramdisk"
  echo "══════════════════════════════════════════════════════════════"
  echo ""

  # ── service ──
  if _is_active; then
    log "Service:  ACTIVE (soulmask_tmpfs.service)"
  else
    log "Service:  INACTIVE (soulmask_tmpfs.service)"
  fi

  # ── tmpfs mount ──
  if mountpoint -q "$RAMDISK" 2>/dev/null; then
    local ro=""
    if findmnt -no OPTIONS "$RAMDISK" 2>/dev/null | grep -q '\<ro\>'; then
      ro="READ-ONLY"
    else
      ro="READ-WRITE ⚠"
    fi
    local usage
    usage=$(df -h "$RAMDISK" 2>/dev/null | tail -1 | awk '{print $3 " / " $2 " (" $5 ")"}')
    log "Tmpfs:    MOUNTED ($ro)  $usage"
  else
    log "Tmpfs:    NOT MOUNTED"
  fi

  # ── bind mounts ──
  if [ -f "$STATE_FILE" ]; then
    local count
    count=$(grep -c 'TARGET=' "$STATE_FILE" 2>/dev/null || echo 0)
    log "Bind mounts: $count in state file"
    if [ "$count" -gt 0 ]; then
      local uuid
      for uuid in $(grep 'TARGET=' "$STATE_FILE" | sed 's|.*/volumes/||;s|/.*||' | sort -u); do
        local n
        n=$(grep -c "volumes/$uuid/" "$STATE_FILE" 2>/dev/null || echo 0)
        log "    $uuid: $n paths"
      done
    fi
  else
    log "Bind mounts: no state file"
  fi

  # ── source ──
  if [ -f "$STATE_FILE" ]; then
    local src
    src=$(grep '^SOURCE_UUID=' "$STATE_FILE" 2>/dev/null | cut -d= -f2)
    [ -n "$src" ] && log "Source:    $src"
  fi

  # ── containers ──
  local running
  running=$(_running_container_count)
  if [ "$running" -gt 0 ]; then
    log "Containers: $running running WSServer instance(s)"
    for cid in $(_running_container_ids); do
      local uuid
      uuid=$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | tr -d '/')
      log "    $uuid"
    done
  else
    log "Containers: NONE running"
  fi

  # ── cgroup slices ──
  echo ""
  log "Cgroup slices:"
  for slice in soulmask_tmpfs.slice soulmask_tmpfs-ZSwapMax0.slice soulmask_tmpfs-ZSwapMax1.slice; do
    local cg_path
    if [ "$slice" = "soulmask_tmpfs.slice" ]; then
      cg_path="/sys/fs/cgroup/$slice"
    else
      cg_path="/sys/fs/cgroup/soulmask_tmpfs.slice/$slice"
    fi
    if [ -d "$cg_path" ]; then
      local mem min max
      mem=$(cat "$cg_path/memory.current" 2>/dev/null | awk '{printf "%.0fM", $1/1048576}')
      min=$(cat "$cg_path/memory.min" 2>/dev/null | awk '{printf "%.0fM", $1/1048576}')
      if [ "$slice" = "soulmask_tmpfs-ZSwapMax0.slice" ]; then
        max=$(cat "$cg_path/memory.zswap.max" 2>/dev/null || true)
        if [ -z "$max" ]; then
          max="zswap=?"
        elif [ "$max" = "0" ]; then
          max="zswap=off"
        else
          max="zswap=$max"
        fi
        log "  $slice  current=$mem  min=$min  $max"
      else
        log "  $slice  current=$mem  min=$min"
      fi
    else
      log "  $slice  (not present)"
    fi
  done

  # ── validation ──
  echo ""
  log "Validation:"
  local issues=0

  if _is_active; then
    # Check tmpfs is actually mounted
    if ! mountpoint -q "$RAMDISK" 2>/dev/null; then
      err "  service is active but $RAMDISK is not mounted"
      issues=$((issues + 1))
    fi
    # Check ro
    if ! findmnt -no OPTIONS "$RAMDISK" 2>/dev/null | grep -q '\<ro\>'; then
      warn "  tmpfs is mounted but NOT read-only — steam updates may corrupt state!"
      issues=$((issues + 1))
    fi
    # Check state file
    if [ ! -f "$STATE_FILE" ]; then
      warn "  state file missing — teardown may not clean up correctly"
      issues=$((issues + 1))
    fi
    # Check bind mounts exist
    if [ -f "$STATE_FILE" ]; then
      while IFS= read -r line; do
        case "$line" in
          TARGET=*)
            local t="${line#TARGET=}"
            if ! mountpoint -q "$t" 2>/dev/null; then
              warn "  bind mount missing: $t"
              issues=$((issues + 1))
            fi
            ;;
        esac
      done < "$STATE_FILE"
    fi

    # Check opted-in instance configs exist
    local inst_count=0
    shopt -s nullglob
    for f in "$INSTANCES_DIR"/*.env; do
      [[ "$f" == *.example ]] && continue
      inst_count=$((inst_count + 1))
    done
    shopt -u nullglob
    if [ "$inst_count" -eq 0 ]; then
      warn "  no TMPFS=1 instance configs found in $INSTANCES_DIR"
      issues=$((issues + 1))
    fi
  fi

  # Check for running containers when tmpfs is inactive (disk state mismatch)
  if ! _is_active && [ "$(_running_container_count)" -gt 0 ]; then
    warn "  tmpfs is INACTIVE but containers are RUNNING"
    warn "  — containers may be using stale tmpfs inodes, or files from disk"
    issues=$((issues + 1))
  fi

  if [ "$issues" -eq 0 ]; then
    log "  no issues found"
  else
    warn "  $issues issue(s) found (see above)"
  fi

  echo ""

  # ── quick actions ──
  if _is_active; then
    log "To tear down:  $(basename "$0") --stop"
  else
    log "To set up:     $(basename "$0") --start"
  fi
  log "For help:      $(basename "$0") --help"
  echo ""
}

# ── start ────────────────────────────────────────────────────────────────────

_start() {
  if _is_active; then
    log "already active"
    return
  fi
  log "activating unified tmpfs (copying ~2.6G, may take a few seconds)..."
  systemctl start "$SERVICE"
  echo ""
  _show_status
  echo ""
  log "ACTION REQUIRED: stop each opted-in (TMPFS=1) Soulmask instance via Wings,"
  log "  then start it again. Running sessions still use disk-backed files until"
  log "  THEIR next container start."
  log "  Wings → server → power → stop → start"
}

# ── stop ─────────────────────────────────────────────────────────────────────

_stop() {
  if ! _is_active; then
    log "already inactive"
    return
  fi

  local running
  running=$(_running_container_count)
  if [ "$running" -gt 0 ]; then
    err "REFUSING — $running Soulmask container(s) are still running:"
    for cid in $(_running_container_ids); do
      local uuid
      uuid=$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | tr -d '/')
      err "  $uuid"
    done
    err ""
    err "Stop all Soulmask containers via Wings before tearing down the ramdisk:"
    err "  Wings → server → power → stop"
    err ""
    err "Then retry: $(basename "$0") --stop"
    exit 1
  fi

  log "deactivating unified tmpfs..."
  systemctl stop "$SERVICE"
  log "INACTIVE — tmpfs unmounted, all paths reverted to disk"
  echo ""
  log "You can now start the ROLE=main server for a game update, then"
  log "re-run $(basename "$0") --start to recreate the ramdisk."
}

# ── main ─────────────────────────────────────────────────────────────────────

ACTION="${1:-}"

case "$ACTION" in
  --help|-h)
    echo "Usage: $(basename "$0") [--start|--stop|--help]"
    echo ""
    echo "Manage the Soulmask unified read-only tmpfs ramdisk."
    echo ""
    echo "With no arguments, shows current status and validates the setup."
    echo ""
    echo "Commands:"
    echo "  --start   Activate the ramdisk (idempotent). Populates tmpfs from"
    echo "            the ROLE=main instance's volume, remounts read-only,"
    echo "            bind-mounts into all TMPFS=1 instances."
    echo "  --stop    Deactivate the ramdisk. REFUSES if any Soulmask container"
    echo "            is still running — stop them via Wings first."
    echo "  --help    Show this message."
    echo ""
    echo "The tmpfs is READ-ONLY during normal operation. Game updates are blocked."
    echo "Manual update procedure:"
    echo "  1. Stop all containers (Wings panel)"
    echo "  2. $(basename "$0") --stop"
    echo "  3. Start ROLE=main server → steamcmd auto-updates on disk"
    echo "  4. Stop main server"
    echo "  5. $(basename "$0") --start   (re-populates from main's updated disk)"
    echo "  6. Start all containers"
    echo ""
    echo "See SOULMASK-TMPFS.md for details."
    exit 0
    ;;
  --start)
    _start
    ;;
  --stop)
    _stop
    ;;
  "")
    _show_status
    ;;
  *)
    echo "Usage: $(basename "$0") [--start|--stop|--help]" >&2
    echo "Try '$(basename "$0") --help' for details." >&2
    exit 1
    ;;
esac
