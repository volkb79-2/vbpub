#!/bin/sh
# wait-for-cdp.sh — bounded poll for the shared Chromium's CDP endpoint to
# come up before execing the given command. Used as a command prefix for
# programs that attach to the shared browser (mcp, devtools-mcp) in
# browser_mode=shared so supervisord's start order (chromium first) does not
# race the attach path. If the browser never comes up within the deadline,
# the wrapped command is exec'd anyway — it will fail its own connection
# attempt and supervisord's autorestart/backoff takes over, so the program
# never wedges in a half-attached state waiting forever.
#
# Usage: wait-for-cdp.sh <cdp-host> <cdp-port> <deadline-seconds> -- <cmd...>
set -e

CDP_HOST="$1"; shift
CDP_PORT="$1"; shift
DEADLINE_S="$1"; shift
if [ "$1" = "--" ]; then shift; fi

i=0
while [ "$i" -lt "$DEADLINE_S" ]; do
  if curl -fsS --max-time 1 "http://${CDP_HOST}:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 1
done

exec "$@"
