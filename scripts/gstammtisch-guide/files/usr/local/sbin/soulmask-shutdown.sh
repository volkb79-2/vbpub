#!/usr/bin/env bash
# ExecStop of soulmask-graceful-stop.service. Saves every running Soulmask
# instance authoritatively via RCON (SaveAndExit), then waits for each
# container to exit; SIGINT as fallback per instance.
#
# N-instance stop ordering (2026-07-07, SOULMASK.md §9 cluster rule): a
# cluster's main instance owns account.db and must stay reachable for
# client instances to accept logins while things are shutting down, so
# clients must go down BEFORE the main. Order applied here:
#   1. ROLE=client and ROLE=standalone instances (either order among themselves)
#   2. ROLE=main instance(s) last
# RCON port/password are read per instance straight from that container's
# own env (docker inspect) — replicated minimally from
# exec-soulmask-rcon.sh's env_of(), NOT calling that script (it also runs an
# interactive connection pre-flight we don't want mid-shutdown).
set -uo pipefail

# shellcheck source=/usr/local/sbin/soulmask-instance-lib.sh
LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  . "$LIB"
else
  echo "[soulmask-shutdown] FATAL: $LIB not found (expected alongside this script)"; exit 1
fi

log(){ echo "[soulmask-shutdown] $*"; }

mapfile -t CIDS < <(soulmask_running_cids)
if [ "${#CIDS[@]}" -eq 0 ]; then
  log "no Soulmask container running"
  exit 0
fi

# Stop Wings (Docker Compose service) BEFORE saving so it can't auto-restart
# any Soulmask instance while we wait for the DB write(s). Wings has
# restart=always; without this step it would relaunch a container mid-
# shutdown, risking a torn save. We match by the Compose service label
# rather than a hardcoded container name.
WINGS_CID=$(docker ps -q --filter 'label=com.docker.compose.service=wings' 2>/dev/null | head -n1)
if [ -n "$WINGS_CID" ]; then
  log "stopping Wings container ($WINGS_CID) to prevent Soulmask auto-restart..."
  docker stop --time=5 "$WINGS_CID" 2>/dev/null || true
fi

_rcon_at() {
  local cid="$1" port pass image
  shift
  port=$(soulmask_rcon_port "$cid"); port="${port:-19000}"
  pass=$(soulmask_rcon_password "$cid")
  image="${RCON_IMAGE:-itzg/rcon-cli}"
  [ -z "$pass" ] && return 1
  docker run --rm --network "container:$cid" "$image" \
    --host 127.0.0.1 --port "$port" --password "$pass" "$@"
}

_stop_one() {
  local cid="$1" uuid
  uuid=$(soulmask_uuid_of "$cid")
  log "stopping instance ${uuid:-$cid} ($cid, role=${ROLE:-?})..."

  log "  requesting RCON SaveAndExit 10 ..."
  if _rcon_at "$cid" SaveAndExit 10 >/dev/null 2>&1; then
    log "  SaveAndExit issued"
  else
    log "  RCON failed/unavailable; sending SIGINT (egg stop = ^C = SIGINT, which also saves)"
    docker kill -s INT "$cid" 2>/dev/null
  fi

  # wait for the DB write + clean exit
  for _ in $(seq 1 150); do
    [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" = "true" ] || { log "  ${uuid:-$cid} stopped cleanly"; return 0; }
    sleep 1
  done
  log "  ${uuid:-$cid} timeout reached; last-resort SIGINT"
  docker kill -s INT "$cid" 2>/dev/null
  sleep 20
  return 0
}

# ── group by role: client/standalone first, main last ─────────────────────
MAIN_CIDS=()
OTHER_CIDS=()
for cid in "${CIDS[@]}"; do
  uuid=$(soulmask_uuid_of "$cid")
  soulmask_load_instance_env "$uuid"
  if [ "$ROLE" = "main" ]; then
    MAIN_CIDS+=("$cid")
  else
    OTHER_CIDS+=("$cid")
  fi
done

for cid in "${OTHER_CIDS[@]:-}"; do
  [ -n "$cid" ] || continue
  uuid=$(soulmask_uuid_of "$cid")
  soulmask_load_instance_env "$uuid"   # re-load so ROLE is set for the log line in _stop_one
  _stop_one "$cid"
done
for cid in "${MAIN_CIDS[@]:-}"; do
  [ -n "$cid" ] || continue
  uuid=$(soulmask_uuid_of "$cid")
  soulmask_load_instance_env "$uuid"
  _stop_one "$cid"
done

exit 0
