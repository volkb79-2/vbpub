#!/usr/bin/env bash
# Host-table-driven SSH wrapper. The provider identity command and remote guard run on every call.
# Usage: hx.sh <host-alias> <remote-shell-command>
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ $# == 2 ]] || { echo "usage: $0 <host-alias> <remote-shell-command>" >&2; exit 2; }
host=$1 remote_command=$2
declare -a ssh_argv=()
if ! controller_host_api_check "$host" >/dev/null; then echo "HOST IDENTITY API CHECK FAILED: $host" >&2; exit 97; fi
if ! controller_host_argv ssh_argv "$host" ssh; then echo 'SSH CONFIG ERROR' >&2; exit 2; fi
guard=$(controller_host_scalar "$host" guard) || { echo 'REMOTE GUARD CONFIG ERROR' >&2; exit 2; }
remote_script=$(printf 'set -e\nif ! { %s\n}; then printf "GUARD-FAIL\\n" >&2; exit 98; fi\n%s\n' "$guard" "$remote_command")
quoted_script=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$remote_script") || exit 2
remote="bash -c $quoted_script"
"${ssh_argv[@]}" "$remote"
