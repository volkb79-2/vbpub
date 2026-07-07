#!/usr/bin/env bash
# soulmask-cgroup-watcher — reconcile loop that applies cgroup knobs to every
# running Soulmask (WSServer) instance once ITS server-list registration
# line appears in its game log.
#
# N-instance (2026-07-07): a Pterodactyl host can run several Soulmask
# containers at once (cluster main + client(s) — see SOULMASK.md §9). Each
# running container (cid) gets its own readiness gate, tracked in
# /run/soulmask-cgroup-watcher/<cid>/:
#   first_seen   epoch seconds when this cid was first observed running
#   done         marker file (empty) — present once this cid has been
#                included in a setup-cgroups.sh application pass
# A cid is considered ready to apply once EITHER (a) its game log shows the
# server-list registration line, or (b) MAX_READY_WAIT has elapsed since
# first_seen — whichever comes first (same 900s apply-anyway timeout the
# single-instance version had, now tracked per container).
#
# Per-instance readiness gating: each apply pass exports SOULMASK_APPLY_CIDS
# (the instances that are ready now + those already applied earlier) so
# setup-cgroups.sh restricts the game knobs to exactly those — a
# still-loading sibling instance is NOT touched until its own registration
# line (or timeout) arrives. Manual `setup-cgroups.sh` runs without that env
# still apply to all running instances.
#
# cids that stop appearing in `docker ps` (container exited/recreated by
# Wings) have their state directory removed, so a restarted instance (new
# cid, per Wings' container-recreation model) starts its readiness wait
# from scratch — same "container recreated → docker logs only contains the
# current boot" guarantee the single-instance version relied on.
#
# Runs as a systemd simple service; never exits normally.
set -uo pipefail

# shellcheck source=/usr/local/sbin/soulmask-instance-lib.sh
LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  . "$LIB"
else
  echo "[cgroup-watcher] FATAL: $LIB not found (expected alongside this script)"; exit 1
fi

POLL=15             # seconds between reconcile passes
MAX_READY_WAIT=900  # apply-anyway timeout per instance (seconds since first seen)
READY_RE='SERVER_LIST.*registe server.*succeed'
STATE_DIR=/run/soulmask-cgroup-watcher

log() { echo "[cgroup-watcher] $(date '+%H:%M:%S') $*"; }

mkdir -p "$STATE_DIR"

while true; do
    mapfile -t RUNNING_CIDS < <(soulmask_running_cids)

    # ── drop state for vanished cids ─────────────────────────────────────────
    for dir in "$STATE_DIR"/*/; do
        [ -d "$dir" ] || continue
        cid="$(basename "$dir")"
        still_running=0
        for c in "${RUNNING_CIDS[@]:-}"; do
            [ "$c" = "$cid" ] && { still_running=1; break; }
        done
        if [ "$still_running" -eq 0 ]; then
            log "instance $cid vanished — dropping state (restart re-triggers its readiness wait)"
            rm -rf "$dir"
        fi
    done

    # ── track first-seen + collect cids that just became ready ──────────────
    READY_NOW=()
    now=$(date +%s)
    for cid in "${RUNNING_CIDS[@]:-}"; do
        [ -n "$cid" ] || continue
        cdir="$STATE_DIR/$cid"
        mkdir -p "$cdir"
        [ -f "$cdir/done" ] && continue     # already applied for this cid

        if [ ! -f "$cdir/first_seen" ]; then
            echo "$now" > "$cdir/first_seen"
            uuid=$(soulmask_uuid_of "$cid")
            log "new instance detected: ${uuid:-$cid} ($cid) — waiting for server-list registration (up to ${MAX_READY_WAIT}s)"
        fi
        first_seen=$(cat "$cdir/first_seen" 2>/dev/null || echo "$now")
        elapsed=$(( now - first_seen ))

        if docker logs "$cid" 2>&1 | grep -qm1 "$READY_RE"; then
            log "instance $cid registered on server list (${elapsed}s after detection)"
            READY_NOW+=("$cid")
        elif [ "$elapsed" -ge "$MAX_READY_WAIT" ]; then
            log "WARN: instance $cid — no registration line within ${MAX_READY_WAIT}s — applying anyway"
            READY_NOW+=("$cid")
        fi
    done

    # ── apply + mark done ─────────────────────────────────────────────────────
    if [ "${#READY_NOW[@]}" -gt 0 ]; then
        # Ready-now + previously-applied instances only; a still-loading
        # sibling keeps its startup freedom until its own gate passes.
        APPLY=("${READY_NOW[@]}")
        for cid in "${RUNNING_CIDS[@]:-}"; do
            [ -n "$cid" ] && [ -f "$STATE_DIR/$cid/done" ] && APPLY+=("$cid")
        done
        log "applying cgroup knobs to ready/applied instances: ${APPLY[*]}"
        SOULMASK_APPLY_CIDS="${APPLY[*]}" /usr/local/sbin/setup-cgroups.sh
        for cid in "${READY_NOW[@]}"; do
            touch "$STATE_DIR/$cid/done"
        done
    fi

    sleep "$POLL"
done
