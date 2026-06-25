#!/usr/bin/env bash
# ExecStop of soulmask-graceful-stop.service. Saves Soulmask authoritatively via
# RCON (SaveAndExit), then waits for the container to exit; SIGINT as fallback.
set -uo pipefail
RCON="${RCON:-/usr/local/sbin/exec-soulmask-rcon.sh}"
log(){ echo "[soulmask-shutdown] $*"; }

CID=""
for c in $(docker ps -q 2>/dev/null); do
  if docker top "$c" 2>/dev/null | grep -q 'WSServer-Linux-Shipping'; then CID="$c"; break; fi
done
[ -z "$CID" ] && { log "no Soulmask container running"; exit 0; }

# Stop Wings (Docker Compose service) BEFORE saving so it can't auto-restart
# Soulmask while we wait for the DB write.  Wings has restart=always; without
# this step it would relaunch the container mid-shutdown, risking a torn save.
# We match by the Compose service label rather than a hardcoded container name.
WINGS_CID=$(docker ps -q --filter 'label=com.docker.compose.service=wings' 2>/dev/null | head -n1)
if [ -n "$WINGS_CID" ]; then
  log "stopping Wings container ($WINGS_CID) to prevent Soulmask auto-restart..."
  docker stop --time=5 "$WINGS_CID" 2>/dev/null || true
fi

log "requesting RCON SaveAndExit 10 ..."
if [ -x "$RCON" ] && "$RCON" SaveAndExit 10; then
  log "SaveAndExit issued"
else
  log "RCON failed/unavailable; sending SIGINT (egg stop = ^C = SIGINT, which also saves)"
  docker kill -s INT "$CID" 2>/dev/null
fi

# wait for the DB write + clean exit
for _ in $(seq 1 150); do
  [ "$(docker inspect -f '{{.State.Running}}' "$CID" 2>/dev/null)" = "true" ] || { log "stopped cleanly"; exit 0; }
  sleep 1
done
log "timeout reached; last-resort SIGINT"
docker kill -s INT "$CID" 2>/dev/null
sleep 20
exit 0
