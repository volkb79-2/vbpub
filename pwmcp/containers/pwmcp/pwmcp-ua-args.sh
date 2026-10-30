#!/bin/sh
# Append the user-agent flag to a command when an override is active (PWMCP-09).
#
#   pwmcp-ua-args.sh <FLAG-PREFIX> -- <command> [args...]
#
# With PWMCP_USER_AGENT_RESOLVED set (exported by entrypoint.sh after validation) this
# execs `<command> [args...] <FLAG-PREFIX><ua>`; unset or empty, it execs the command
# unchanged, so the default behaviour is byte-identical to before. The UA is passed as ONE
# argv element, so its spaces and parentheses never go through shell word-splitting.
# Example prefixes: --user-agent=  (chromium, playwright-mcp)
#                   --chrome-arg=--user-agent=  (chrome-devtools-mcp)
set -e
prefix="$1"
if [ "$2" != "--" ]; then
  echo "usage: pwmcp-ua-args.sh PREFIX -- COMMAND [ARGS...]" >&2
  exit 2
fi
shift 2
if [ -n "${PWMCP_USER_AGENT_RESOLVED:-}" ]; then
  exec "$@" "${prefix}${PWMCP_USER_AGENT_RESOLVED}"
fi
exec "$@"
