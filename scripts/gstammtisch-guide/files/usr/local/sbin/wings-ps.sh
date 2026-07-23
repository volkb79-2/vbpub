#!/usr/bin/env bash
# wings-ps.sh — `docker ps` for Wings-managed containers, but with the
# panel's display name instead of the bare server UUID.
#
# Wings names every container after the server UUID it runs (its own
# internal lookup key — do NOT `docker rename` these, Wings won't find them
# again). That makes `docker ps` unreadable once more than one server is
# running. There is no local cache of the panel's friendly name (checked
# 2026-07-21: no docker label, no /var/lib/pterodactyl/states.json field —
# that file only has uuid:status) — so this resolves it live, per UUID, via
# the SAME node-to-panel remote API call Wings itself uses
# (GET /api/remote/servers/<uuid>, node token from config.yml), reading
# settings.meta.name out of the response. No hand-maintained mapping file to
# go stale when a server is renamed or a new one is added.
set -euo pipefail

CONFIG="${WINGS_CONFIG:-/etc/pterodactyl/config.yml}"
TOKEN_ID=$(awk '/^token_id:/{print $2}' "$CONFIG")
TOKEN=$(awk '/^token:/{print $2}' "$CONFIG")
REMOTE=$(awk '/^remote:/{print $2}' "$CONFIG")

is_uuid() {
  [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]
}

resolve_name() {
  curl -fs --max-time 5 \
    -H "Authorization: Bearer ${TOKEN_ID}.${TOKEN}" \
    -H "Accept: application/vnd.pterodactyl.v1+json" \
    "${REMOTE}/api/remote/servers/$1" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["settings"]["meta"]["name"])' 2>/dev/null
}

printf '%-18s %-36s %-22s %s\n' "NAME" "UUID" "STATUS" "CONTAINER"
docker ps -a --format '{{.Names}}|{{.Status}}|{{.ID}}' | while IFS='|' read -r name status cid; do
  is_uuid "$name" || continue
  server_name=$(resolve_name "$name")
  printf '%-18s %-36s %-22s %s\n' "${server_name:-?}" "$name" "$status" "$cid"
done
