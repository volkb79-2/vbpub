#!/usr/bin/env bash
# =============================================================================
# bk-lane.sh — REMOTE-LANES-BUILDKITE.md seam 4: trigger run-gate lanes on a
# remote Buildkite agent from THIS host, and collect what came back.
#
#   bk-lane.sh run <lane>...            create a build for HEAD, wait, exit 0
#                                       only if the build passed
#   bk-lane.sh status <build-number>    print the build's state
#   bk-lane.sh collect <build-number> <dir>
#                                       download the build's artifacts into
#                                       <dir>/<commit>/<artifact path>
#   bk-lane.sh --dry-run <verb> ...     print every curl invocation it WOULD
#                                       make (token redacted) and make NO
#                                       network call
#
# The lane still runs as `./run-gate.py <lane>` on the agent: this script only
# creates the build and reads its result. Lane SELECTION is passed to the
# pipeline generator (seam 2, tools/buildkite/pipeline.sh) as the build's
# env.RUN_GATE_LANES — this script never reads run-gate.toml and never decides
# what a lane is.
#
# Environment contract:
#   BK_ORG          REQUIRED — Buildkite organization slug
#   BK_PIPELINE     REQUIRED — pipeline slug
#   BK_TOKEN_FILE   optional — default ~/.config/buildkite/api-token; the file
#                   must be mode 0600 or this script refuses (exit 2). Token
#                   scopes needed: read_builds, write_builds, read_artifacts.
#   BK_POLL_SECONDS optional — poll interval for `run`, default 30
#
# REST paths, verified against the vendor docs on 2026-09-03:
#   POST /v2/organizations/{org}/pipelines/{pipeline}/builds
#   GET  /v2/organizations/{org}/pipelines/{pipeline}/builds/{number}
#   GET  /v2/organizations/{org}/pipelines/{pipeline}/builds/{number}/artifacts
#        (https://buildkite.com/docs/apis/rest-api/artifacts — "List artifacts
#         for a build"; each artifact object carries id, path, filename, state,
#         file_size, sha1sum and download_url)
#
# Dependencies: bash, coreutils, git, curl, python3 (stdlib json only). `jq` is
# deliberately NOT assumed.
#
# STATUS: only --dry-run has been exercised (tests/test_buildkite_tools.py).
# No live build has been created by this script yet.
# =============================================================================
set -euo pipefail

PROG=${0##*/}

TERMINAL_STATES="passed failed canceled blocked skipped not_run waiting_failed"

usage() {
    cat <<'EOF'
usage: bk-lane.sh [--dry-run] run <lane>...
       bk-lane.sh [--dry-run] status <build-number>
       bk-lane.sh [--dry-run] collect <build-number> <dir>
       bk-lane.sh --help

  run       create a build for HEAD (commit + branch from git) with
            env.RUN_GATE_LANES set to the named lanes, then poll until the
            state is terminal. Exit 0 only when the state is "passed".
  status    print the build's state and exit 0.
  collect   download every artifact of the build into <dir>/<commit>/...
  --dry-run print the curl invocations that would run (token redacted) and
            touch no network.

environment: BK_ORG and BK_PIPELINE are required; BK_TOKEN_FILE defaults to
~/.config/buildkite/api-token and must be mode 0600; BK_POLL_SECONDS
defaults to 30.

terminal build states: passed failed canceled blocked skipped not_run
waiting_failed.  exit codes: 0 ok/passed, 1 the build did not pass, 2 refused.
EOF
}

die() { printf '%s: %s\n' "$PROG" "$1" >&2; exit 2; }

# --- display helpers -------------------------------------------------------
sq() {   # single-quote one argument for display
    local s=${1//\'/\'\\\'\'}
    printf "'%s'" "$s"
}

show_curl() {   # print a curl invocation with the bearer token redacted
    local out="curl" a
    for a in "$@"; do
        if [ "$a" = "$AUTH_HEADER" ]; then
            a="Authorization: Bearer <redacted>"
        fi
        out="$out $(sq "$a")"
    done
    printf '%s\n' "$out"
}

# --- arguments -------------------------------------------------------------
DRY_RUN=no
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=yes; shift ;;
        --help|-h) usage; exit 0 ;;
        --) shift; break ;;
        -*) die "unknown option '$1' (see --help)" ;;
        *) break ;;
    esac
done
[ "$#" -gt 0 ] || { usage >&2; die "no verb given (run|status|collect)"; }
verb=$1; shift

# --- environment -----------------------------------------------------------
org=${BK_ORG-}
pipeline=${BK_PIPELINE-}
[ -n "$org" ] || die "BK_ORG is unset or empty — the Buildkite organization slug has no default"
[ -n "$pipeline" ] || die "BK_PIPELINE is unset or empty — the pipeline slug has no default"

token_file=${BK_TOKEN_FILE:-$HOME/.config/buildkite/api-token}
[ -f "$token_file" ] || die "token file '$token_file' does not exist (create it, mode 0600, or set BK_TOKEN_FILE)"
mode=$(stat -c '%a' "$token_file")
[ "$mode" = "600" ] || die "token file '$token_file' is mode $mode; it must be 0600 (chmod 600 '$token_file')"
TOKEN=$(cat "$token_file")
[ -n "$TOKEN" ] || die "token file '$token_file' is empty"
AUTH_HEADER="Authorization: Bearer $TOKEN"

poll_seconds=${BK_POLL_SECONDS:-30}
case "$poll_seconds" in
    ""|*[!0-9]*) die "BK_POLL_SECONDS='$poll_seconds' is not a positive integer number of seconds" ;;
esac
[ "$poll_seconds" -gt 0 ] || die "BK_POLL_SECONDS='$poll_seconds' is not a positive integer number of seconds"

API="https://api.buildkite.com/v2/organizations/$org/pipelines/$pipeline/builds"

# --- small json readers (stdlib python3; jq is not assumed) ----------------
json_field() {   # $1 = key; reads the JSON object on stdin
    RG_KEY=$1 python3 -c '
import json, os, sys
doc = json.load(sys.stdin)
key = os.environ["RG_KEY"]
if key not in doc:
    sys.exit("bk-lane.sh: response has no %r field" % key)
print(doc[key])
'
}

check_number() {
    case "${1-}" in
        ""|*[!0-9]*) die "build number '${1-}' is not a positive integer" ;;
    esac
}

# --- verbs -----------------------------------------------------------------
do_run() {
    [ "$#" -gt 0 ] || die "run needs at least one lane name"
    local lane
    for lane in "$@"; do
        case "$lane" in
            *[!A-Za-z0-9._-]*) die "lane name '$lane' has a character outside [A-Za-z0-9._-]" ;;
        esac
    done
    local lanes="$*"

    git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git work tree — 'run' takes the commit and branch from git"
    local commit branch
    commit=$(git rev-parse HEAD)
    branch=$(git rev-parse --abbrev-ref HEAD)
    [ "$branch" != "HEAD" ] || die "HEAD is detached; Buildkite requires a branch — check out a branch before triggering"

    local body
    body=$(RG_COMMIT="$commit" RG_BRANCH="$branch" RG_LANES="$lanes" python3 -c '
import json, os
print(json.dumps({
    "commit": os.environ["RG_COMMIT"],
    "branch": os.environ["RG_BRANCH"],
    "message": "run-gate: " + os.environ["RG_LANES"],
    "env": {"RUN_GATE_LANES": os.environ["RG_LANES"]},
}, sort_keys=True))')

    local post=(-fsS -X POST "$API" -H "$AUTH_HEADER"
                -H "Content-Type: application/json" -d "$body")

    if [ "$DRY_RUN" = yes ]; then
        printf 'would create a build for %s on branch %s with lanes: %s\n' \
            "$commit" "$branch" "$lanes"
        show_curl "${post[@]}"
        printf 'then poll every %ss until the state is terminal (%s):\n' \
            "$poll_seconds" "$TERMINAL_STATES"
        show_curl -fsS "$API/<build-number>" -H "$AUTH_HEADER"
        return 0
    fi

    local number
    number=$(curl "${post[@]}" | json_field number)
    printf 'build %s for %s (branch %s), lanes: %s\n' "$number" "$commit" "$branch" "$lanes"

    local state
    while :; do
        state=$(curl -fsS "$API/$number" -H "$AUTH_HEADER" | json_field state)
        case " $TERMINAL_STATES " in
            *" $state "*) break ;;
        esac
        printf 'state: %s — polling again in %ss\n' "$state" "$poll_seconds"
        sleep "$poll_seconds"
    done
    printf 'build %s state: %s\n' "$number" "$state"
    [ "$state" = passed ] || return 1
    return 0
}

do_status() {
    check_number "${1-}"
    local number=$1
    if [ "$DRY_RUN" = yes ]; then
        show_curl -fsS "$API/$number" -H "$AUTH_HEADER"
        return 0
    fi
    curl -fsS "$API/$number" -H "$AUTH_HEADER" | json_field state
}

do_collect() {
    check_number "${1-}"
    local number=$1
    local dir=${2-}
    [ -n "$dir" ] || die "collect needs a destination directory: collect <build-number> <dir>"

    if [ "$DRY_RUN" = yes ]; then
        printf 'would read the build to learn its commit:\n'
        show_curl -fsS "$API/$number" -H "$AUTH_HEADER"
        printf 'would list the artifacts of build %s:\n' "$number"
        show_curl -fsS "$API/$number/artifacts" -H "$AUTH_HEADER"
        printf 'then, for each artifact in that listing, into %s/<commit>/<path>:\n' "$dir"
        show_curl -fsSL "<artifact download_url>" -H "$AUTH_HEADER" -o "$dir/<commit>/<artifact path>"
        return 0
    fi

    local commit
    commit=$(curl -fsS "$API/$number" -H "$AUTH_HEADER" | json_field commit)
    local dest="$dir/$commit"
    mkdir -p "$dest"

    local listing
    listing=$(curl -fsS "$API/$number/artifacts" -H "$AUTH_HEADER")

    local pairs
    pairs=$(printf '%s' "$listing" | python3 -c '
import json, sys
for a in json.load(sys.stdin):
    path, url = a["path"], a["download_url"]
    if path.startswith("/") or ".." in path.split("/") or "\t" in path or "\n" in path:
        sys.exit("bk-lane.sh: refusing artifact path %r" % path)
    print("%s\t%s" % (path, url))
')
    local path url count=0
    while IFS=$'\t' read -r path url; do
        [ -n "$path" ] || continue
        mkdir -p "$dest/$(dirname "$path")"
        curl -fsSL "$url" -H "$AUTH_HEADER" -o "$dest/$path"
        printf '%s\n' "$dest/$path"
        count=$((count + 1))
    done <<EOF
$pairs
EOF
    printf 'collected %s artifact(s) of build %s into %s\n' "$count" "$number" "$dest"
}

case "$verb" in
    run) do_run "$@" ;;
    status) do_status "$@" ;;
    collect) do_collect "$@" ;;
    *) die "unknown verb '$verb' (run|status|collect)" ;;
esac
