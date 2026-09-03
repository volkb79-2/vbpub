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
#   BK_MAX_WAIT_MINUTES
#                   optional — `run` only: how long to keep polling, default
#                   300 (the §3 step's own timeout). Exceeded -> exit 3 naming
#                   the build number and the last state, so `status`/`collect`
#                   can pick that build up later (§4.4). The budget counts the
#                   time this script spends SLEEPING between polls, which is
#                   what an unattended `run` actually burns; a single request
#                   that hangs is curl's business, not this counter's.
#   BK_QUEUE        optional — `run` only: sent as env.RUN_GATE_QUEUE in the
#                   create-build body, which overrides the pipeline's own
#                   env.RUN_GATE_QUEUE for that build (a build's env wins over
#                   the pipeline's), so one command moves a run to another
#                   host's queue without editing the pipeline. Unset = the
#                   pipeline's default queue, and the key is not sent at all.
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
# EXIT CODES — the whole contract, true of every path:
#   0  the verb did what it says (for `run`: the build passed)
#   1  the build did not pass (a terminal state other than `passed`) — this
#      code means THAT and nothing else
#   2  refused: every refusal goes through `die`, including a malformed or
#      short API response, a `commit` or artifact `path` this script will not
#      use as a path component, and every bad argument or environment value
#   3  gave up waiting (BK_MAX_WAIT_MINUTES); the build is still out there and
#      the message names its number and last state
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
            touch no network. Accepted ANYWHERE in the arguments, before or
            after the verb — it is the one flag whose whole job is "make no
            network call", so a misordered one must not be swallowed.

environment: BK_ORG and BK_PIPELINE are required; BK_TOKEN_FILE defaults to
~/.config/buildkite/api-token and must be mode 0600; BK_POLL_SECONDS
defaults to 30; BK_MAX_WAIT_MINUTES defaults to 300; BK_QUEUE, if set, is
sent as env.RUN_GATE_QUEUE in the create-build body and overrides the
pipeline's queue for that build.

terminal build states: passed failed canceled blocked skipped not_run
waiting_failed.

exit codes: 0 ok (for `run`: passed) | 1 the build did not pass, and nothing
else | 2 refused (every refusal, via `die`) | 3 gave up waiting, the message
naming the build number and last state so `status`/`collect` can follow up.
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
# --dry-run is honoured ANYWHERE in argv: `run --dry-run lint` used to make
# `--dry-run` a lane name and create a REAL build, which is the worst possible
# reading of the one flag that exists to make no network call. Everything after
# a literal `--` is a positional, so a lane genuinely called `--dry-run` (there
# is none — lane names may not start with `-`, see do_run) stays expressible.
DRY_RUN=no
positional=()
end_of_options=no
for arg in "$@"; do
    if [ "$end_of_options" = yes ]; then
        positional+=("$arg")
        continue
    fi
    case "$arg" in
        --dry-run) DRY_RUN=yes ;;
        --help|-h) usage; exit 0 ;;
        --) end_of_options=yes ;;
        -*) die "unknown option '$arg' (see --help)" ;;
        *) positional+=("$arg") ;;
    esac
done
set -- ${positional[@]+"${positional[@]}"}
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

# A positive integer with NO leading zero: "0300" read as octal 192 by a YAML
# parser is the sibling bug in pipeline.sh, and "07" is never what anyone means.
check_positive_int() {   # $1 = value, $2 = what it is (for the message)
    case "$1" in
        ""|*[!0-9]*) die "$2='$1' is not a positive integer" ;;
        0) die "$2='0' is not a positive integer" ;;
        0*) die "$2='$1' has a leading zero — write it plainly; a leading zero is read as octal by a YAML parser and is never what was meant" ;;
    esac
}

poll_seconds=${BK_POLL_SECONDS:-30}
check_positive_int "$poll_seconds" BK_POLL_SECONDS
max_wait_minutes=${BK_MAX_WAIT_MINUTES:-300}
check_positive_int "$max_wait_minutes" BK_MAX_WAIT_MINUTES

API="https://api.buildkite.com/v2/organizations/$org/pipelines/$pipeline/builds"

# --- small json readers (stdlib python3; jq is not assumed) ----------------
# Every one of these is called through `api_field`, so a malformed response, a
# missing field or a failed request is a `die` (exit 2) — never a bare exit 1,
# which is reserved for "the build did not pass".
json_field() {   # $1 = key; reads the JSON object on stdin
    RG_KEY=$1 python3 -c '
import json, os, sys
try:
    doc = json.load(sys.stdin)
except ValueError as exc:
    sys.exit("response is not JSON: %s" % exc)
key = os.environ["RG_KEY"]
if not isinstance(doc, dict) or key not in doc:
    sys.exit("response has no %r field" % key)
print(doc[key])
'
}

api_field() {   # $1 = url, $2 = key -> the field's value on stdout, or die
    local value
    if ! value=$(curl -fsS "$1" -H "$AUTH_HEADER" | json_field "$2"); then
        die "GET $1 did not yield a usable '$2' (see the message above)"
    fi
    printf '%s\n' "$value"
}

check_number() {
    case "${1-}" in
        ""|*[!0-9]*) die "build number '${1-}' is not a positive integer" ;;
    esac
}

# Anything used as a PATH COMPONENT is gated on the script's own charset before
# it is used. `commit` comes straight from the build response and Buildkite
# documents it as a free-form "Ref, SHA or tag" chosen by whoever created the
# build — a `../../..` there would write outside the destination directory, so
# it is checked exactly like a lane name or a queue name.
check_path_component() {   # $1 = value, $2 = what it is
    case "$1" in
        "") die "$2 from the API response is empty; refusing to use it as a directory name" ;;
        *[!A-Za-z0-9._-]*) die "$2 '$1' from the API response has a character outside [A-Za-z0-9._-]; refusing to use it as a directory name" ;;
        .|..) die "$2 '$1' from the API response is not usable as a directory name" ;;
    esac
}

# --- verbs -----------------------------------------------------------------
do_run() {
    [ "$#" -gt 0 ] || die "run needs at least one lane name"
    local lane seen=""
    for lane in "$@"; do
        case "$lane" in
            -*) die "lane name '$lane' starts with '-'; lane names cannot. If you meant the flag, it is '--dry-run' (accepted anywhere in the arguments)" ;;
            *[!A-Za-z0-9._-]*) die "lane name '$lane' has a character outside [A-Za-z0-9._-]" ;;
        esac
        case " $seen " in
            *" $lane "*) die "lane '$lane' is named twice; RUN_GATE_LANES would run it twice on the agent" ;;
        esac
        seen="$seen $lane"
    done
    local lanes="$*"

    git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git work tree — 'run' takes the commit and branch from git"
    local commit branch
    commit=$(git rev-parse HEAD)
    branch=$(git rev-parse --abbrev-ref HEAD)
    [ "$branch" != "HEAD" ] || die "HEAD is detached; Buildkite requires a branch — check out a branch before triggering"

    # BK_QUEUE, when set, rides in the build's env as RUN_GATE_QUEUE: a
    # build's env overrides the pipeline's, so this moves ONE run to another
    # host's queue without editing the pipeline. Unset -> the key is absent
    # and the pipeline's own default queue stands.
    local queue=${BK_QUEUE-}
    case "$queue" in
        *[!A-Za-z0-9._-]*) die "BK_QUEUE='$queue' has a character outside [A-Za-z0-9._-]; a Buildkite queue name does not need one" ;;
    esac

    local body
    body=$(RG_COMMIT="$commit" RG_BRANCH="$branch" RG_LANES="$lanes" \
           RG_QUEUE="$queue" python3 -c '
import json, os
build_env = {"RUN_GATE_LANES": os.environ["RG_LANES"]}
if os.environ["RG_QUEUE"]:
    build_env["RUN_GATE_QUEUE"] = os.environ["RG_QUEUE"]
print(json.dumps({
    "commit": os.environ["RG_COMMIT"],
    "branch": os.environ["RG_BRANCH"],
    "message": "run-gate: " + os.environ["RG_LANES"],
    "env": build_env,
}, sort_keys=True))')

    local post=(-fsS -X POST "$API" -H "$AUTH_HEADER"
                -H "Content-Type: application/json" -d "$body")

    if [ "$DRY_RUN" = yes ]; then
        printf 'would create a build for %s on branch %s with lanes: %s\n' \
            "$commit" "$branch" "$lanes"
        if [ -n "$queue" ]; then
            printf "the build's env overrides the pipeline queue: RUN_GATE_QUEUE=%s\n" "$queue"
        fi
        show_curl "${post[@]}"
        printf 'then poll every %ss, for at most %s minutes, until the state is terminal (%s):\n' \
            "$poll_seconds" "$max_wait_minutes" "$TERMINAL_STATES"
        show_curl -fsS "$API/<build-number>" -H "$AUTH_HEADER"
        return 0
    fi

    local number
    if ! number=$(curl "${post[@]}" | json_field number); then
        die "creating the build did not yield a usable 'number' (see the message above)"
    fi
    check_path_component "$number" "build number"
    printf 'build %s for %s (branch %s), lanes: %s\n' "$number" "$commit" "$branch" "$lanes"

    local state waited=0 budget=$((max_wait_minutes * 60))
    while :; do
        state=$(api_field "$API/$number" state)
        case " $TERMINAL_STATES " in
            *" $state "*) break ;;
        esac
        if [ "$waited" -ge "$budget" ]; then
            printf 'build %s is still %s after %s minutes of waiting; giving up on the WAIT, not on the build — follow it with: %s status %s\n' \
                "$number" "$state" "$max_wait_minutes" "$PROG" "$number" >&2
            return 3
        fi
        printf 'state: %s — polling again in %ss\n' "$state" "$poll_seconds"
        sleep "$poll_seconds"
        waited=$((waited + poll_seconds))
    done
    printf 'build %s state: %s\n' "$number" "$state"
    [ "$state" = passed ] || return 1
    return 0
}

do_status() {
    check_number "${1-}"
    local number=$1
    shift
    [ "$#" -eq 0 ] || die "status takes exactly one argument (the build number); it does not know what to do with: $*"
    if [ "$DRY_RUN" = yes ]; then
        show_curl -fsS "$API/$number" -H "$AUTH_HEADER"
        return 0
    fi
    api_field "$API/$number" state
}

do_collect() {
    check_number "${1-}"
    local number=$1
    local dir=${2-}
    [ -n "$dir" ] || die "collect needs a destination directory: collect <build-number> <dir>"
    shift 2
    [ "$#" -eq 0 ] || die "collect takes exactly two arguments (the build number and a directory); it does not know what to do with: $*"

    if [ "$DRY_RUN" = yes ]; then
        printf 'would read the build to learn its commit:\n'
        show_curl -fsS "$API/$number" -H "$AUTH_HEADER"
        printf 'would list the artifacts of build %s:\n' "$number"
        show_curl -fsS "$API/$number/artifacts" -H "$AUTH_HEADER"
        printf 'then, for each artifact in that listing, into %s/<commit>/<path>:\n' "$dir"
        show_curl -fsSL "<artifact download_url>" -H "$AUTH_HEADER" -o "$dir/<commit>/<artifact path>"
        return 0
    fi

    # The commit is a PATH COMPONENT taken from the response, so it is gated
    # before `mkdir` ever sees it (B1: `"commit": "../../../escape"` used to
    # write outside <dir>).
    local commit
    commit=$(api_field "$API/$number" commit)
    check_path_component "$commit" "the build's commit"
    local dest="$dir/$commit"
    mkdir -p "$dest"

    local listing pairs
    if ! listing=$(curl -fsS "$API/$number/artifacts" -H "$AUTH_HEADER"); then
        die "GET $API/$number/artifacts failed"
    fi
    # python does the JSON only; every refusal below is the shell's, so it is a
    # `die` (exit 2) and not the exit 1 that means "the build did not pass".
    if ! pairs=$(printf '%s' "$listing" | python3 -c '
import json, sys
try:
    doc = json.load(sys.stdin)
except ValueError as exc:
    sys.exit("artifact listing is not JSON: %s" % exc)
if not isinstance(doc, list):
    sys.exit("artifact listing is not a JSON array")
for a in doc:
    if not isinstance(a, dict) or "path" not in a or "download_url" not in a:
        sys.exit("an artifact object has no path/download_url")
    path, url = a["path"], a["download_url"]
    if "\t" in path or "\n" in path or "\t" in url or "\n" in url:
        sys.exit("artifact path or url contains a tab or newline: %r" % path)
    print("%s\t%s" % (path, url))
'); then
        die "the artifact listing of build $number is unusable (see the message above)"
    fi

    local path url count=0
    while IFS=$'\t' read -r path url; do
        [ -n "$path" ] || continue
        # Containment, in the script's own idiom: no absolute path, no ".."
        # component, nothing that escapes <dest>.
        case "$path" in
            /*) die "refusing artifact path '$path': it is absolute and would write outside $dest" ;;
        esac
        case "/$path/" in
            */../*) die "refusing artifact path '$path': a '..' component would write outside $dest" ;;
        esac
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
