#!/usr/bin/env bash
# nyxloomd supervisor loop with a CRASH-LOOP BREAKER.
#
# Context (docs/runtime-process-model.md §2, P37): tini is container PID 1
# (compose `init: true`); THIS script is the daemon's parent — a supervisor
# loop, NOT the daemon itself (no `exec` of the daemon), so a daemon crash
# reparents its live wrapper children to tini and the respawned daemon
# re-adopts them from their on-disk pid/wrapper.pid. That gives crash-SAFETY.
#
# What this adds (2026-07-21): crash-loop DETECTION. The old body was a bare
#   while true; do <daemon>; sleep 2; done
# which respawns unconditionally — a PERSISTENT startup fault (missing dep,
# bad config, corrupt state) then crash-loops every 2s forever, loudly doing
# nothing. Instead: count CONSECUTIVE RAPID failures (each running less than
# MIN_HEALTHY_SECS) and, after MAX_RAPID_FAILURES of them, give up — write a
# clear log line and PARK the container for operator inspection.
#
# Why PARK (sleep infinity), not exit: the container runs `restart:
# unless-stopped`. A plain `exit` would let Docker recreate the container,
# resetting this breaker into a slow *container-level* crash-loop (~1/min) —
# it would never actually stay down. Parking holds ONE inspectable container
# Up-but-UNHEALTHY (the 8942 healthcheck fails), with the failure reason in
# `docker logs`, and does not touch the restart policy (so a host reboot still
# legitimately retries, and a manual `docker restart` after a fix works).
#
# A run that stays up >= MIN_HEALTHY_SECS is "healthy enough" and RESETS the
# counter — so a genuine one-off crash of a long-lived daemon still auto-heals
# (the original crash-safety intent), and only back-to-back fast deaths trip.
#
# Tunables (env, all optional):
#   NYXLOOM_SUPERVISE_MAX_FAILURES     consecutive rapid failures to trip (default 3)
#   NYXLOOM_SUPERVISE_MIN_HEALTHY_SECS run this long => "healthy", reset (default 60)
#   NYXLOOM_SUPERVISE_RESPAWN_DELAY    sleep between respawns (default 2)
#   NYXLOOM_SUPERVISE_DAEMON_CMD       daemon command (default: the real daemon)
#                                      — a test seam; harmless in prod.
set -u

STATE_DIR="${NYXLOOM_STATE:-$HOME/.local/state/nyxloom}"
PIDFILE="$STATE_DIR/daemon/nyxloomd.pid"
MAX_RAPID_FAILURES="${NYXLOOM_SUPERVISE_MAX_FAILURES:-3}"
MIN_HEALTHY_SECS="${NYXLOOM_SUPERVISE_MIN_HEALTHY_SECS:-60}"
RESPAWN_DELAY="${NYXLOOM_SUPERVISE_RESPAWN_DELAY:-2}"
DAEMON_CMD="${NYXLOOM_SUPERVISE_DAEMON_CMD:-/opt/nyxloom-venv/bin/python -m nyxloom.cli daemon}"

log() { echo "nyxloomd-supervise: $*" >&2; }

# Clear a stale pidfile ONCE up front (same as the old command) — NOT per
# iteration: a mid-run crash must leave the pidfile so the respawned daemon can
# re-adopt its still-live wrappers.
rm -f "$PIDFILE"

fails=0
while true; do
  start=$(date +%s)
  # shellcheck disable=SC2086  # DAEMON_CMD is an intentional word-split command.
  $DAEMON_CMD
  rc=$?
  ran=$(( $(date +%s) - start ))

  if [ "$ran" -ge "$MIN_HEALTHY_SECS" ]; then
    if [ "$fails" -ne 0 ]; then
      log "recovered (ran ${ran}s >= ${MIN_HEALTHY_SECS}s) — resetting failure counter"
    fi
    fails=0
  else
    fails=$(( fails + 1 ))
    log "daemon exited rc=${rc} after ${ran}s — rapid failure ${fails}/${MAX_RAPID_FAILURES}"
  fi

  if [ "$fails" -ge "$MAX_RAPID_FAILURES" ]; then
    log "${MAX_RAPID_FAILURES} consecutive rapid failures — refusing to respawn."
    log "The daemon is DOWN ON PURPOSE. Fix the fault (see the last traceback above),"
    log "then 'docker restart nyxloom-prod-nyxloomd'. Holding the container alive and"
    log "UNHEALTHY for inspection (not flapping)."
    exec sleep infinity
  fi

  sleep "$RESPAWN_DELAY"
done
