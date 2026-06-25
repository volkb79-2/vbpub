#!/usr/bin/env bash
# exec-soulmask-rcon.sh [-d] <rcon command [args...]>   (-d = debug)
# Runs any Soulmask RCON command against the running container and prints the reply.
#
# Command reference: https://saraserenity.net/soulmask/remote_console.php
#   save (no exit):   SaveWorld 0
#   save + shutdown:  SaveAndExit <seconds>     (cancel with: StopCloseServer)
#   players:          List_OnlinePlayers        (alias: lp)
#   message:          broadcast <text>
#
# Design notes:
#  - Soulmask speaks Source RCON over TCP and enforces an IP WHITELIST. We run the
#    rcon client INSIDE the Soulmask container's own network namespace
#    (--network container:<cid>) so the connection is 127.0.0.1 (loopback), which
#    is whitelist-friendly and needs no IP discovery.
#  - The server process WSServer-Linux-Shipping is a CHILD of the Pterodactyl
#    entrypoint, so it never shows in `docker ps {{.Command}}` (also truncated).
#    We detect it with `docker top`, which sees child processes.
set -uo pipefail   # deliberately NOT -e — we handle errors with clear messages
RCON_IMAGE="${RCON_IMAGE:-itzg/rcon-cli}"
DEBUG=0; [ "${1:-}" = "-d" ] && { DEBUG=1; shift; }
log(){ echo "[rcon] $*" >&2; }
dbg(){ [ "$DEBUG" = 1 ] && echo "[rcon:debug] $*" >&2; }
die(){ echo "[rcon:ERROR] $*" >&2; exit "${2:-1}"; }

command -v docker >/dev/null || die "docker not in PATH"

# 1) find the Soulmask container by its real running process
CID=""
for c in $(docker ps -q); do
    if docker top "$c" 2>/dev/null | grep -q 'WSServer-Linux-Shipping'; then CID="$c"; break; fi
    dbg "$c: no WSServer"
done
[ -z "$CID" ] && CID=$(docker ps --filter ancestor=ghcr.io/ptero-eggs/steamcmd:debian -q | head -n1)
[ -z "$CID" ] && die "Soulmask container not found (is the server running?)"
log "container: $CID ($(docker ps --format '{{.Names}}' --filter id="$CID"))"

# 2) RCON port + password from the container env (Wings injects egg variables)
env_of(){ docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CID" | sed -n "s/^$1=//p" | head -n1; }
PORT=$(env_of RCON_PORT); PORT="${PORT:-19000}"
PASS=$(env_of RCON_PASSWORD)
dbg "RCON_PORT=$PORT  RCON_PASSWORD=$([ -n "$PASS" ] && echo "<set,${#PASS} chars>" || echo "<EMPTY>")"
[ -z "$PASS" ] && die "RCON_PASSWORD empty on container env — is RCON set in the egg variables?"

# forward ALL args to rcon-cli (it joins them into the command -> multi-param works)
rcon(){ docker run --rm --network "container:$CID" "$RCON_IMAGE" \
          --host 127.0.0.1 --port "$PORT" --password "$PASS" "$@"; }

# 3) connection/auth pre-flight (benign, read-only)
log "connection test: List_OnlinePlayers"
if ! out=$(rcon List_OnlinePlayers 2>&1); then die "RCON test FAILED:
$out" 2; fi
log "connection OK"; dbg "reply: $out"

# 4) run the requested command
if [ "$#" -gt 0 ]; then log "> $*"; rcon "$@"; else log "(connection test only; no command given)"; fi
