#!/usr/bin/env bash
# Record what the mdt-buildkitd.service process was STARTED from (MDT-HS-07 H-66): the digest of the rendered
# unit and config at the moment of the (re)start, in /run/mdt/buildkitd.started-digest. install.sh, check.sh and the
# reconcile compare it with the rendered files to report "restart pending". Called by ExecStartPost.
set -uo pipefail
LIB="${LIB:-/usr/local/sbin/mdt-container-io-caps.lib.sh}"
log() { echo "[mdt-buildkitd-stamp] $*" >&2; }
[ -r "$LIB" ] || { log "ERROR: cannot read $LIB"; exit 1; }
# shellcheck disable=SC1090
. "$LIB"
stamp=$(_mdt_buildkitd_stamp_file)
mkdir -p "$(dirname "$stamp")" || exit 1
_mdt_files_digest "${MDT_BUILDKITD_FILES[@]}" > "$stamp.tmp" && mv -f "$stamp.tmp" "$stamp"
