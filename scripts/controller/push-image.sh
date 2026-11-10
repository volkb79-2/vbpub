#!/usr/bin/env bash
# Stream a local Docker image to a configured runner host and verify it is visible there.
# Usage: push-image.sh <host-alias> <image:tag>
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ $# == 2 ]] || { echo "usage: $0 <host-alias> <image:tag>" >&2; exit 2; }
host=$1 image=$2
[[ $image =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]*$ ]] || { echo 'invalid image reference' >&2; exit 2; }
hx=${HX_SCRIPT:-$SCRIPT_DIR/hx.sh}
docker save "$image" | gzip -1 | "$hx" "$host" 'gunzip | docker load'
statuses=("${PIPESTATUS[@]}")
printf 'PUSH-EXIT=%s/%s/%s\n' "${statuses[0]}" "${statuses[1]}" "${statuses[2]}"
for rc in "${statuses[@]}"; do (( rc == 0 )) || exit 1; done
"$hx" "$host" "docker image ls $(printf '%q' "$image") --format '{{.Repository}}:{{.Tag}} {{.ID}}'"
