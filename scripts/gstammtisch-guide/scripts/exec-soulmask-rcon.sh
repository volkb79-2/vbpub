#!/usr/bin/env bash
# exec-soulmask-rcon.sh [-d] [-i|--interactive] [-c|--container SEL] <rcon command [args...]>
#   -d                debug (verbose diagnostics on stderr)
#   -i/--interactive  drop into an interactive rcon-cli session (one held-open
#                     connection, reads commands from stdin) instead of the
#                     default one-shot request/reply. Ignores any trailing
#                     command args. Needed for 'help', which puts Soulmask's
#                     RCON into a server-side paginated "QUERY INTERACTIVE
#                     MODE" expecting further input (n / <page#> / q) on the
#                     SAME connection — the default one-shot mode sends one
#                     command and disconnects, so it only ever prints page 1.
#   -c/--container    select which Soulmask server when several run on this
#                     host (Wings names each container after its server UUID).
#                     SEL = server-UUID prefix, container-id prefix, or any
#                     substring of the container name. Without -c the FIRST
#                     WSServer container is used and a notice lists any others.
# Runs Soulmask RCON commands against the running container and prints the reply.
#
# Command reference: https://saraserenity.net/soulmask/remote_console.php
#   save (no exit):   SaveWorld 0
#   save + shutdown:  SaveAndExit <seconds>     (cancel with: StopCloseServer)
#   players:          List_OnlinePlayers        (alias: lp)
#   message:          broadcast <text>
#   paginated help:   help   -> use -i/--interactive (see above), or it will
#                     silently truncate to page 1 in the default one-shot mode.
#
# IMPORTANT: RCON responsiveness is NOT a server-health signal. The RCON
# listener runs on its own thread and keeps answering even while the game
# thread itself is stalled (e.g. swap/disk-bound under memory pressure) — a
# fast reply here does not mean the game tick is healthy. Use `ServerFPS`
# (or the zswap monitor's rf_d/s disk-refault column) as the real probe,
# not RCON latency.
#
# Design notes:
#  - Soulmask speaks Source RCON over TCP and enforces an IP WHITELIST. We run the
#    rcon client INSIDE the Soulmask container's own network namespace
#    (--network container:<cid>) so the connection is 127.0.0.1 (loopback), which
#    is whitelist-friendly and needs no IP discovery.
#  - The server process WSServer-Linux-Shipping is a CHILD of the Pterodactyl
#    entrypoint, so it never shows in `docker ps {{.Command}}` (also truncated).
#    We detect it with `docker top`, which sees child processes.
#  - itzg/rcon-cli run with NO command argument drops into its own interactive
#    REPL, reading commands from stdin over ONE held-open connection — that is
#    what -i/--interactive uses, via `docker run -it` so stdin/stdout/tty are
#    passed through to it.
set -uo pipefail   # deliberately NOT -e — we handle errors with clear messages
RCON_IMAGE="${RCON_IMAGE:-itzg/rcon-cli}"

# Leading flags, any order: -d, -i/--interactive, -c/--container SEL.
# Anything else (including the rcon command itself) stops flag parsing.
DEBUG=0; INTERACTIVE=0; SELECTOR=""
while :; do
  case "${1:-}" in
    -d)               DEBUG=1; shift ;;
    -i|--interactive) INTERACTIVE=1; shift ;;
    -c|--container)
      SELECTOR="${2:-}"
      [ -z "$SELECTOR" ] && { echo "[rcon:ERROR] -c/--container needs a value" >&2; exit 1; }
      shift 2 ;;
    *)                break ;;
  esac
done

log(){ echo "[rcon] $*" >&2; }
dbg(){ [ "$DEBUG" = 1 ] && echo "[rcon:debug] $*" >&2; }
die(){ echo "[rcon:ERROR] $*" >&2; exit "${2:-1}"; }

command -v docker >/dev/null || die "docker not in PATH"

# 1) find the Soulmask container by its real running process, honouring -c.
#    A -c SELECTOR matches a container-id prefix or a name substring (Wings
#    names containers by server UUID, so a UUID prefix is a name prefix).
_sel_match(){ # $1=cid $2=name — succeeds when no selector or selector matches
  [ -z "$SELECTOR" ] && return 0
  case "$1" in "$SELECTOR"*) return 0 ;; esac
  case "$2" in *"$SELECTOR"*) return 0 ;; esac
  return 1
}
CID=""; OTHERS=""
for c in $(docker ps -q); do
    name=$(docker ps --format '{{.Names}}' --filter id="$c")
    if ! _sel_match "$c" "$name"; then dbg "$c ($name): does not match -c $SELECTOR"; continue; fi
    if docker top "$c" 2>/dev/null | grep -q 'WSServer-Linux-Shipping'; then
        if [ -z "$CID" ]; then CID="$c"; CNAME="$name"; else OTHERS="$OTHERS $c($name)"; fi
        continue
    fi
    dbg "$c: no WSServer"
done
if [ -z "$CID" ] && [ -z "$SELECTOR" ]; then
    CID=$(docker ps --filter ancestor=ghcr.io/ptero-eggs/steamcmd:debian -q | head -n1)
    [ -n "$CID" ] && CNAME=$(docker ps --format '{{.Names}}' --filter id="$CID")
fi
if [ -z "$CID" ]; then
    [ -n "$SELECTOR" ] && die "no WSServer container matches -c '$SELECTOR' (is that server running?)"
    die "Soulmask container not found (is the server running?)"
fi
[ -n "$OTHERS" ] && log "NOTICE: multiple WSServer containers — using $CID ($CNAME); ignoring:$OTHERS. Select one with -c/--container."
log "container: $CID ($CNAME)"

# 2) RCON port + password from the container env (Wings injects egg variables)
env_of(){ docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CID" | sed -n "s/^$1=//p" | head -n1; }
PORT=$(env_of RCON_PORT); PORT="${PORT:-19000}"
PASS=$(env_of RCON_PASSWORD)
dbg "RCON_PORT=$PORT  RCON_PASSWORD=$([ -n "$PASS" ] && echo "<set,${#PASS} chars>" || echo "<EMPTY>")"
[ -z "$PASS" ] && die "RCON_PASSWORD empty on container env — is RCON set in the egg variables?"

# forward ALL args to rcon-cli (it joins them into the command -> multi-param works)
rcon(){ docker run --rm --network "container:$CID" "$RCON_IMAGE" \
          --host 127.0.0.1 --port "$PORT" --password "$PASS" "$@"; }

# 3) connection/auth pre-flight (benign, read-only). Runs even in interactive
#    mode so a misconfigured RCON_PASSWORD/port fails fast with a clear
#    message instead of handing the terminal to a rcon-cli session that will
#    just hang or reject auth silently.
log "connection test: List_OnlinePlayers"
if ! out=$(rcon List_OnlinePlayers 2>&1); then die "RCON test FAILED:
$out" 2; fi
log "connection OK"; dbg "reply: $out"

# 4) interactive mode: hand the terminal to rcon-cli's own REPL over ONE
#    held-open connection (no command argument = interactive per itzg/rcon-cli).
#    This is what paginated commands like 'help' need: further input (n /
#    <page#> / q) on the SAME connection, which one-shot mode cannot provide.
if [ "$INTERACTIVE" = 1 ]; then
  [ "$#" -gt 0 ] && log "note: -i/--interactive ignores trailing args ($*); type commands at the rcon-cli prompt instead"
  log "interactive session: type commands at the prompt (e.g. 'help', then n/<page#>/q to page); Ctrl-D or 'quit' to exit"
  exec docker run -it --rm --network "container:$CID" "$RCON_IMAGE" \
        --host 127.0.0.1 --port "$PORT" --password "$PASS"
fi

# 5) one-shot: run the requested command
if [ "$#" -gt 0 ]; then log "> $*"; rcon "$@"; else log "(connection test only; no command given)"; fi
