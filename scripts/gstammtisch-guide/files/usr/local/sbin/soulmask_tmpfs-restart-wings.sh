#!/usr/bin/env bash
# ExecStartPost/ExecStopPost for soulmask_tmpfs.service.
#
# Belt-and-suspenders alongside the wings docker-compose.yml propagation fix
# (rslave on the /var/lib/pterodactyl bind): a plain `docker restart` tears
# down and rebuilds a container's mount namespace from scratch via runc's
# stop/start lifecycle, so it re-snapshots the host's CURRENT mount table
# regardless of propagation mode. Cheap insurance against wings ever again
# carrying ghost copies of tmpfs bind mounts the host has since torn down
# (see SOULMASK-TMPFS.md, 2026-07-31 incident).
#
# Finds the wings container by its compose service label rather than a
# hardcoded name/path, so this keeps working if the compose project moves.
#
# No `-e`, deliberately: this runs as ExecStartPost/ExecStopPost on
# soulmask_tmpfs.service, and a failure here must never fail the tmpfs
# toggle it's attached to (Type=oneshot would report the whole unit as
# failed even though setup/teardown already succeeded). The one case this
# script treats as a real error (multiple wings matches, below) exits
# non-zero on purpose — an ambiguous match is worse silently guessed at than
# loudly flagged.
set -uo pipefail

log() { echo "[soulmask_tmpfs] $*"; }
err() { echo "[soulmask_tmpfs] ERROR: $*" >&2; }

# Before=docker.service means this fires at boot before dockerd exists, and
# the reverse order on shutdown means dockerd is already gone by the time
# ExecStop/ExecStopPost run. Both are expected, not a problem — skip quietly
# rather than logging a false-alarm warning on every boot/shutdown.
systemctl is-active --quiet docker.service || {
  log "docker.service not active (expected during boot/shutdown) — skipping"
  exit 0
}

mapfile -t cids < <(docker ps -q --filter "label=com.docker.compose.service=wings")

if [ "${#cids[@]}" -eq 0 ]; then
  log "WARNING: no running container labeled com.docker.compose.service=wings — restart it manually if tmpfs mounts were toggled"
  exit 0
fi

if [ "${#cids[@]}" -gt 1 ]; then
  err "multiple containers labeled com.docker.compose.service=wings (${cids[*]}) — refusing to guess which one to restart"
  exit 1
fi

cid="${cids[0]}"
log "restarting wings ($cid) to refresh its view of the host mount table"
if docker restart "$cid" >/dev/null; then
  log "wings restarted"
else
  log "WARNING: failed to restart wings ($cid) — restart it manually"
fi

exit 0
