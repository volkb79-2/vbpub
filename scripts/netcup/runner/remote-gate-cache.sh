#!/usr/bin/env bash
# Repository-owned successor for the controller helper that runs
# `scripts/netcup/runner/remote-gate.py` from a cached copy of a ref (CTL-12).
#
# The helper this replaces refreshed `<cache>.new` and `mv`-ed it into place, so
# two launches in the same second staged into ONE name: the observed batch of
# three lost two lanes to `mv: cannot move ... .new` reported as "cannot read
# the tool from origin/main". The fix is not a stagger; it is unique staging,
# an atomic publish, a serialised refresh and two refusals that cannot be
# confused with each other.
#
# Properties this script guarantees:
#   * UNIQUE STAGING. Every invocation stages its own `.<name>.stage.<pid>.<rand>`
#     file, so two invocations can never write into one path. Nothing ever moves
#     another invocation's staging file, and a leftover staging file from a crash
#     is invisible to every later invocation.
#   * CONTENT-ADDRESSED PUBLISH. The published name is `<cache-dir>/remote-gate-<blob>.py`
#     (the git blob id of the tool at the resolved commit), published with one
#     atomic `rename`. Two invocations racing on the same ref therefore publish
#     the same bytes under the same name, and a `rename` onto an existing path is
#     atomic, so a reader never sees a partial file.
#   * ONE REFRESH AT A TIME. The fetch-and-publish section is serialised with a
#     kernel `flock` on `<cache-dir>/.refresh.lock`; the lock is released by the
#     kernel if this process dies, so a killed invocation cannot strand it.
#   * TWO DISTINCT REFUSALS. `could not refresh the cache` (exit 3: the staging
#     write, the rename, or a git read failed) is never reported as `the tool is
#     absent from <ref>` (exit 4: the ref resolved and that path is not in it),
#     and an unresolvable ref is its own refusal (exit 5). Each names the ref and
#     path it tried.
#   * THE IDENTITY IS EMITTED. The refreshed copy's identity (resolved commit and
#     the tool's blob id) is printed as `key=value` lines, so a caller can record
#     which revision actually ran in its own evidence.
#
# Usage: remote-gate-cache.sh <ref> [options]
#   --repo DIR        repository to read from (default: $REMOTE_GATE_REPO, else
#                     this script's repository root)
#   --tool PATH       tool path inside the ref (default scripts/netcup/runner/remote-gate.py)
#   --cache-dir DIR   where the published copies live (default $REMOTE_GATE_CACHE_DIR,
#                     else $CONTROLLER_HOME/remote-gate-cache, else ~/controller/remote-gate-cache)
#
# Print (on success): `cache=<path>`, `ref=<ref>`, `commit=<full sha>`,
# `identity=blob:<blob sha>`, and each key is on its own line so a caller can
# append them to a run record verbatim.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

usage() {
  echo "usage: $0 <ref> [--repo DIR] [--tool PATH] [--cache-dir DIR]" >&2
  exit 2
}

ref=''
[[ -n ${repo:-} ]] || repo=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
tool=scripts/netcup/runner/remote-gate.py
cache_dir=${REMOTE_GATE_CACHE_DIR:-}
positional=()
while (( $# > 0 )); do
  case $1 in
    --repo) (( $# >= 2 )) || usage; repo=$2; shift 2 ;;
    --tool) (( $# >= 2 )) || usage; tool=$2; shift 2 ;;
    --cache-dir) (( $# >= 2 )) || usage; cache_dir=$2; shift 2 ;;
    -h|--help) usage ;;
    *) positional+=("$1"); shift ;;
  esac
done
(( ${#positional[@]} == 1 )) || usage
ref=${positional[0]}
[[ -n $ref && $ref != -* ]] || usage
[[ $tool == /* ]] && usage
if [[ -z $cache_dir ]]; then
  if [[ -n ${CONTROLLER_HOME:-} ]]; then
    cache_dir=$CONTROLLER_HOME/remote-gate-cache
  else
    cache_dir=${HOME:?HOME must be set}/controller/remote-gate-cache
  fi
fi

# A refusal names the ref, the path and the directory it tried: the whole point
# is that a reader can tell "I could not refresh" from "there is nothing there".
refresh_refusal() {
  local detail=$1
  echo "remote-gate-cache: could not refresh the cache: $detail" >&2
  echo "  ref: $ref" >&2
  echo "  tool: $tool" >&2
  echo "  cache dir: $cache_dir" >&2
  exit 3
}
absent_refusal() {
  local resolved=$1
  echo "remote-gate-cache: the tool is absent from ref $ref (resolved $resolved)" >&2
  echo "  tool: $tool" >&2
  exit 4
}

git_read() { git -C "$repo" "$@"; }

# Resolve the ref first: an unresolvable ref is neither a refresh failure nor an
# absence, and folding it into either would repeat the defect this fixes.
if ! commit=$(git_read rev-parse --verify "$ref^{commit}" 2>/dev/null) || [[ -z $commit ]]; then
  echo "remote-gate-cache: cannot resolve ref $ref in $repo" >&2
  exit 5
fi
# The blob id is the tool file's content identity at that commit. Resolving it
# separately is what makes "absent" decidable: the ref exists, the path does not.
if ! blob=$(git_read rev-parse "$commit:$tool" 2>/dev/null) || [[ -z $blob ]]; then
  absent_refusal "$commit"
fi

published=$cache_dir/remote-gate-$blob.py

published_is_valid() {
  # The name says what the content must be, so the content is VERIFIED rather
  # than trusted by name: a truncated copy, a foreign file or a leftover from a
  # crash must be replaced, not published. `git hash-object` gives the same blob
  # id git itself uses, so the comparison cannot disagree with the resolution
  # above.
  local candidate=$1 text=''
  [[ -s $candidate ]] || return 1
  text=$(git -C "$repo" hash-object --stdin < "$candidate" 2>/dev/null) || return 1
  [[ $text == "$blob" ]]
}

emit() {
  printf 'cache=%s\nref=%s\ncommit=%s\nidentity=blob:%s\n' \
    "$published" "$ref" "$commit" "$blob"
}

if published_is_valid "$published"; then
  # Content-addressed and immutable: a copy already published for this blob IS
  # this blob, so a second invocation never needs the refresh at all.
  emit
  exit 0
fi

mkdir -p -- "$cache_dir" || refresh_refusal "cannot create $cache_dir"
if ! exec {refresh_fd}>>"$cache_dir/.refresh.lock"; then
  refresh_refusal "cannot open $cache_dir/.refresh.lock"
fi
# Serialise the refresh; the kernel drops the lock when this process dies. The
# wait is bounded so a stuck holder cannot block a lane batch forever, and it is
# overridable only so a test can exercise that refusal without waiting.
lock_wait=${REMOTE_GATE_CACHE_LOCK_WAIT:-60}
[[ $lock_wait =~ ^[0-9]+$ ]] || { echo 'REMOTE_GATE_CACHE_LOCK_WAIT must be integer seconds' >&2; exit 2; }
if ! flock -w "$lock_wait" "$refresh_fd"; then
  exec {refresh_fd}>&-
  refresh_refusal "another invocation held $cache_dir/.refresh.lock for more than $lock_wait s"
fi
if published_is_valid "$published"; then
  # Another invocation published the same blob while this one waited.
  exec {refresh_fd}>&-
  emit
  exit 0
fi

# Unique staging name: pid + random suffix, in the cache dir so the publish is a
# same-filesystem rename. Nothing else in this script, and nothing in any other
# invocation, ever writes this path.
stage=$cache_dir/.remote-gate-$blob.stage.$$.$RANDOM$RANDOM
cleanup() {
  local fd=${refresh_fd:-}
  if [[ -f $stage ]]; then rm -f -- "$stage" 2>/dev/null || :; fi
  [[ $fd =~ ^[0-9]+$ ]] && eval "exec $fd>&-" 2>/dev/null || :
}
trap cleanup EXIT
if ! git_read cat-file blob "$blob" > "$stage" 2>/dev/null; then
  refresh_refusal "cannot read blob $blob from $repo"
fi
if [[ ! -s $stage ]]; then
  refresh_refusal "the fetched copy of $tool is empty"
fi
chmod 0755 -- "$stage" 2>/dev/null || refresh_refusal "cannot make $stage executable"
# One atomic rename publishes it; a concurrent publisher of the same blob wrote
# identical bytes, so whichever rename lands last is still this blob.
if ! mv -f -T -- "$stage" "$published" 2>/dev/null; then
  refresh_refusal "cannot publish $published"
fi
exec {refresh_fd}>&-
emit
