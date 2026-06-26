#!/usr/bin/env bash
# soulmask-cgroup-watcher — persistent daemon that applies cgroup knobs to
# Soulmask only after the server is fully initialised (RCON responds).
#
# Handles server restarts: after each container exit it loops back and waits
# for the next one.  Runs as a systemd simple service; never exits normally.
#
# Why wait for RCON before applying constraints:
#   UE4 startup needs more RAM than steady-state play.  Applying memory.high
#   too early (during cold-disk load) can crash or deadlock the server.
#   Waiting for RCON guarantees the world is loaded and the game loop is live.

set -uo pipefail

POLL_CONTAINER=15   # seconds between container-detection polls
POLL_RCON=20        # seconds between RCON probes while waiting for ready
MAX_RCON_WAIT=900   # give up waiting for RCON after 15 min; apply anyway

log() { echo "[cgroup-watcher] $(date '+%H:%M:%S') $*"; }

while true; do
    # ── 1. wait for Soulmask container ──────────────────────────────────────
    log "waiting for Soulmask container..."
    CID=""
    while [ -z "$CID" ]; do
        for c in $(docker ps -q 2>/dev/null); do
            if docker top "$c" 2>/dev/null | grep -q 'WSServer-Linux-Shipping'; then
                CID="$c"; break
            fi
        done
        [ -z "$CID" ] && sleep $POLL_CONTAINER
    done
    log "container found: $CID"

    # ── 2. wait for RCON to respond (= server fully initialised) ────────────
    log "waiting for RCON (up to ${MAX_RCON_WAIT}s)..."
    t0=$(date +%s)
    while true; do
        if /usr/local/sbin/exec-soulmask-rcon.sh List_OnlinePlayers &>/dev/null; then
            log "RCON ready ($(($(date +%s) - t0))s after container start)"
            break
        fi
        elapsed=$(( $(date +%s) - t0 ))
        if [ $elapsed -ge $MAX_RCON_WAIT ]; then
            log "WARN: RCON did not respond in ${MAX_RCON_WAIT}s — applying cgroup knobs anyway"
            break
        fi
        log "  RCON not ready yet (${elapsed}s elapsed)..."
        sleep $POLL_RCON
    done

    # ── 3. apply cgroup knobs ───────────────────────────────────────────────
    /usr/local/sbin/setup-cgroups.sh

    # ── 4. wait for this container to stop, then loop ───────────────────────
    log "cgroup knobs applied. Watching container $CID for exit..."
    docker wait "$CID" 2>/dev/null || true
    log "container $CID exited — looping to watch for next start"
    sleep 5
done
