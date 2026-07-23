#!/usr/bin/env bash
# soulmask-instance-lib.sh — shared helpers for N-instance Soulmask host
# scripts. SOURCE this file (`. /usr/local/sbin/soulmask-instance-lib.sh`);
# it defines functions/variables only and has no side effects, so it is safe
# to source under any of this project's `set -uo pipefail` / `set -euo
# pipefail` callers.
#
# Container <-> instance UUID: Pterodactyl Wings names each Soulmask
# container after its server UUID (`docker inspect -f '{{.Name}}'` == the
# UUID with a leading slash — verified live, 2026-07-07), so the docker name
# IS the instance identifier. No separate mapping table is needed.
#
# Config layout (see /etc/gstammtisch/instance-defaults.env and
# /etc/gstammtisch/instances.d/<uuid>.env — SOULMASK.md "Multi-instance
# operations"):
#   instance-defaults.env   — SOULMASK_MIN/LOW/HIGH/WRITEBACK, ROLE, PAK_RAMDISK
#   instances.d/<uuid>.env  — per-instance overrides (one file per server)

GSTAMMTISCH_ETC="${GSTAMMTISCH_ETC:-/etc/gstammtisch}"
CG_ROOT="${CG_ROOT:-/sys/fs/cgroup}"

# --- discovery --------------------------------------------------------------

# Print one docker container ID per line for every RUNNING container whose
# `docker top` output shows the Soulmask game process. Mirrors the detection
# pattern used throughout this project (exec-soulmask-rcon.sh et al.): the
# game binary is a child of the container entrypoint, so `docker top` (which
# sees child processes) is required — `docker ps {{.Command}}` only shows
# the (truncated) entrypoint.
soulmask_running_cids() {
  local c
  for c in $(docker ps -q 2>/dev/null); do
    # A --pid=host container (admin/nsenter shells, monitors) sees EVERY host
    # process in `docker top`, so it would false-match the game binary below --
    # and the watcher would eventually apply game-tier knobs (memory.min=6G!)
    # to a random admin container. Skip host-PID containers outright.
    [ "$(docker inspect -f '{{.HostConfig.PidMode}}' "$c" 2>/dev/null)" = "host" ] && continue
    docker top "$c" 2>/dev/null | grep -q 'WSServer-Linux-Shipping' && echo "$c"
  done
}

# Resolve a container ID to its Pterodactyl server UUID (== docker name).
soulmask_uuid_of() {
  docker inspect -f '{{.Name}}' "$1" 2>/dev/null | tr -d '/'
}

# Resolve a container ID to its unified cgroup-v2 scope directory, the same
# two ways setup-cgroups.sh always has:
#   1. /proc/<pid>/cgroup (fast path)
#   2. find under CG_ROOT by container-id substring (fallback)
# Prints the path and returns 0 on success; returns 1 if unresolvable.
soulmask_cgroup_of() {
  local cid="$1" pid scope
  pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null || true)
  scope=""
  [ -n "$pid" ] && scope="$CG_ROOT$(awk -F: '/^0::/{print $3}' "/proc/$pid/cgroup" 2>/dev/null)"
  [ -d "$scope" ] || scope=$(find "$CG_ROOT" -type d -name "*${cid}*" 2>/dev/null | head -n1)
  if [ -n "$scope" ] && [ -d "$scope" ]; then
    echo "$scope"
    return 0
  fi
  return 1
}

# --- per-instance config -----------------------------------------------------

# Load defaults + per-instance override for UUID into the CURRENT shell.
# Always resets the known vars first so a previous instance's values can't
# leak into the next iteration of a `for uuid in ...` loop in the caller.
# Deliberately does NOT honour ambient environment overrides of these vars
# any more (single-instance scripts used to allow e.g.
# `SOULMASK_MIN=8G setup-cgroups.sh` for ad-hoc testing) — with N instances
# a single ambient value can no longer mean "the" instance's floor. Use
# instances.d/<uuid>.env (or instance-defaults.env for a host-wide change)
# instead. SYSTEM_SLICE_MIN / SOULMASK_SLICE_MIN (host-wide ancestor floors,
# not per-instance) still honour ambient env overrides in setup-cgroups.sh.
soulmask_load_instance_env() {
  local uuid="$1"
  unset SOULMASK_MIN SOULMASK_LOW SOULMASK_HIGH SOULMASK_WRITEBACK ROLE PAK_RAMDISK STATIC_RAMDISK
  # shellcheck disable=SC1091
  [ -f "$GSTAMMTISCH_ETC/instance-defaults.env" ] && . "$GSTAMMTISCH_ETC/instance-defaults.env"
  # shellcheck disable=SC1091
  [ -f "$GSTAMMTISCH_ETC/instances.d/${uuid}.env" ] && . "$GSTAMMTISCH_ETC/instances.d/${uuid}.env"
  # Hard-coded fallbacks in case both files are missing (defensive — keeps
  # scripts working stand-alone / before install.sh has run).
  SOULMASK_MIN="${SOULMASK_MIN:-6G}"
  SOULMASK_LOW="${SOULMASK_LOW:-12G}"
  SOULMASK_HIGH="${SOULMASK_HIGH:-7G}"
  SOULMASK_WRITEBACK="${SOULMASK_WRITEBACK:-1}"
  ROLE="${ROLE:-standalone}"
  PAK_RAMDISK="${PAK_RAMDISK:-0}"
  # STATIC_RAMDISK: opt into the generalized read-only install-content
  # ramdisk (soulmask-static-ramdisk.service — Engine/Binaries/bundled Steam
  # libs, NOT the pak, which stays on the separate, pre-existing
  # soulmask-pak-ramdisk.service / PAK_RAMDISK). See SOULMASK.md.
  STATIC_RAMDISK="${STATIC_RAMDISK:-0}"
}

# --- RCON port/password -------------------------------------------------------
# Replicated minimally from exec-soulmask-rcon.sh's env_of() — deliberately
# NOT calling that script here: it also runs an interactive connection
# pre-flight, which soulmask-shutdown.sh doesn't want mid-shutdown.
soulmask_rcon_port() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" 2>/dev/null \
    | sed -n 's/^RCON_PORT=//p' | head -n1
}
soulmask_rcon_password() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" 2>/dev/null \
    | sed -n 's/^RCON_PASSWORD=//p' | head -n1
}

# --- -c <uuid-or-prefix> selection for single-instance-scoped tools ----------
# (soulmask-mempress.sh / soulmask-pak-mempress.sh / soulmask-startup-cgroup.sh)
# Prints the resolved CID on stdout. On ambiguity/no-match, prints guidance to
# stderr and returns 1. With no selector: auto-selects if exactly one
# instance is running, otherwise lists candidates and asks for -c.
soulmask_select_instance() {
  local want="${1:-}" cid uuid
  local -a cids=() matches=()
  while IFS= read -r cid; do [ -n "$cid" ] && cids+=("$cid"); done < <(soulmask_running_cids)

  if [ "${#cids[@]}" -eq 0 ]; then
    echo "No running Soulmask (WSServer) containers found." >&2
    return 1
  fi

  if [ -z "$want" ]; then
    if [ "${#cids[@]}" -eq 1 ]; then
      echo "${cids[0]}"
      return 0
    fi
    echo "Multiple Soulmask instances running — pass -c <uuid-or-prefix>:" >&2
    for cid in "${cids[@]}"; do
      echo "  $(soulmask_uuid_of "$cid")  (container $cid)" >&2
    done
    return 1
  fi

  for cid in "${cids[@]}"; do
    uuid=$(soulmask_uuid_of "$cid")
    case "$uuid" in
      "$want"*) matches+=("$cid") ;;
    esac
  done

  case "${#matches[@]}" in
    1) echo "${matches[0]}"; return 0 ;;
    0) echo "No running instance matches -c '$want'." >&2; return 1 ;;
    *) echo "Selector '$want' matches multiple running instances — be more specific:" >&2
       for cid in "${matches[@]}"; do echo "  $(soulmask_uuid_of "$cid")" >&2; done
       return 1 ;;
  esac
}
