#!/usr/bin/env bash
# =============================================================================
# pipeline.sh — REMOTE-LANES-BUILDKITE.md seam 2: the Buildkite pipeline
# generator for run-gate lanes.
#
# Emits, on stdout, a Buildkite pipeline document with ONE command step per
# selected lane, in the exact shape documented in REMOTE-LANES-BUILDKITE.md §3.
# The stored pipeline configuration is the two-line upload step:
#
#     steps:
#       - label: ":pipeline: lanes"
#         command: "tools/buildkite/pipeline.sh | buildkite-agent pipeline upload"
#
# LANE METADATA COMES FROM `./run-gate.py --list` AND NOWHERE ELSE. CONSUMERS.md
# "Anti-goals" forbids a second parser of run-gate.toml, so this script never
# opens that file. The listing's contract, verified against run-gate.py rev 33
# `cmd_list()` and its own usage line, is one lane per line:
#
#     name<TAB>kind<TAB>environment
#
# and nothing else. A line carrying a fourth column is REFUSED rather than
# silently mis-read: that would mean the contract moved and this generator has
# to be re-read against it.
#
# What the listing does NOT say is which lanes are remote-capable — there is no
# such column, and inventing one here would be exactly the second parser the
# anti-goal forbids. So an empty RUN_GATE_LANES selects EVERY lane the listing
# shows (both of run-gate's kinds, "command" and "assay"); name the lanes you
# want in RUN_GATE_LANES if that is not what you mean. See §3 of the manual.
#
# Environment contract:
#   RUN_GATE_QUEUE            REQUIRED. The agent queue (a host's queue, e.g.
#                             "gate-<host>"). Also names the concurrency group.
#                             Unset/empty -> exit 2 naming it.
#   RUN_GATE_LANES            Optional. Space-separated lane names. Empty or
#                             unset = every lane in the listing. A name the
#                             listing does not carry -> exit 2 naming it.
#   RUN_GATE_TIMEOUT_MINUTES  Optional, default 300. Positive integer.
#
# Argument: $1 = the project directory holding run-gate.py, relative to the
# checkout root the agent builds in (default "."). It is used BOTH to invoke
# `--list` here and, verbatim, inside the emitted `command` and
# `artifact_paths` — so pass the path as the agent will see it.
#
# Dependencies: bash and the project's own run-gate.py (python3, stdlib only).
# No awk/jq/yaml tooling: the document is small enough to print exactly.
#
# There is no --dry-run: this script only ever writes to stdout, starts no
# container and touches no network. Running it IS the rehearsal.
# =============================================================================
set -euo pipefail

PROG=${0##*/}

usage() {
    cat <<'EOF'
usage: pipeline.sh [PROJECT_DIR]        (default: .)
       pipeline.sh --help

Emits Buildkite pipeline YAML on stdout: one command step per selected
run-gate lane, each running `./run-gate.py <lane>` on the agent.

environment:
  RUN_GATE_QUEUE            required — agent queue; also the concurrency group
  RUN_GATE_LANES            optional — space-separated lane names
                            (empty = every lane `./run-gate.py --list` shows)
  RUN_GATE_TIMEOUT_MINUTES  optional — per-step timeout, default 300

exit codes: 0 emitted, 2 refused (bad environment, unknown lane, unusable
listing). Lane metadata is read ONLY from `./run-gate.py --list`.
EOF
}

die() { printf '%s: %s\n' "$PROG" "$1" >&2; exit 2; }

# --- arguments -------------------------------------------------------------
project="."
case "${1-}" in
    --help|-h) usage; exit 0 ;;
    "") ;;
    -*) die "unknown option '$1' (only --help); the sole argument is the project directory" ;;
    *) project=$1 ;;
esac
if [ "$#" -gt 1 ]; then
    die "takes at most one argument (the project directory), got $#"
fi

# Trailing slashes off, so "proj/" and "proj" emit the same document.
while [ "$project" != "/" ] && [ "${project%/}" != "$project" ]; do
    project=${project%/}
done
# shellcheck disable=SC1003  # '\' here is a literal backslash to match on, not an escape
case "$project" in
    *'"'*|*'$'*|*'`'*|*'\'*|*$'\n'*)
        die "project directory '$project' contains a character that cannot be quoted safely inside the emitted YAML (\" \$ \` \\\\ or newline)" ;;
esac
[ -d "$project" ] || die "project directory '$project' does not exist"
[ -x "$project/run-gate.py" ] || die "'$project/run-gate.py' is not an executable file — this generator reads lane metadata only through it"

# Path prefix used inside the emitted step. "." means "the checkout root IS the
# project", so neither a `cd` nor an artifact prefix is emitted for it.
if [ "$project" = "." ]; then
    cd_prefix=""
    art_prefix=""
else
    cd_prefix="cd $project && "
    art_prefix="$project/"
fi

# --- environment -----------------------------------------------------------
queue=${RUN_GATE_QUEUE-}
[ -n "$queue" ] || die "RUN_GATE_QUEUE is unset or empty — it names the agent queue AND the concurrency group that enforces one gate container per host; there is no default"
case "$queue" in
    *[!A-Za-z0-9._-]*)
        die "RUN_GATE_QUEUE='$queue' has a character outside [A-Za-z0-9._-]; a Buildkite queue name does not need one and it cannot be quoted safely here" ;;
esac

timeout=${RUN_GATE_TIMEOUT_MINUTES:-300}
case "$timeout" in
    ""|*[!0-9]*) die "RUN_GATE_TIMEOUT_MINUTES='$timeout' is not a positive integer number of minutes" ;;
    0) die "RUN_GATE_TIMEOUT_MINUTES='0' is not a positive integer number of minutes" ;;
    # A leading zero is not cosmetic here: this value is emitted into YAML, and
    # a YAML parser reads `timeout_in_minutes: 0300` as OCTAL 192.
    0*) die "RUN_GATE_TIMEOUT_MINUTES='$timeout' has a leading zero — write it plainly; YAML reads a leading-zero integer as octal (0300 is 192, not 300)" ;;
esac

# --- the listing -----------------------------------------------------------
if ! listing=$(cd "$project" && ./run-gate.py --list); then
    die "'$project/run-gate.py --list' failed — fix the project's lane config first; this generator has no second way to learn the lanes"
fi

available=""
while IFS=$'\t' read -r name kind environment; do
    if [ -z "$name" ]; then
        continue
    fi
    case "$environment" in
        *$'\t'*)
            die "'$project/run-gate.py --list' produced a line with more than the three documented columns (name<TAB>kind<TAB>environment): '$name/$kind/$environment' — the listing contract moved; re-read this generator against it" ;;
    esac
    if [ -z "$kind" ] || [ -z "$environment" ]; then
        die "'$project/run-gate.py --list' produced a line that is not name<TAB>kind<TAB>environment: '$name'"
    fi
    case "$name" in
        *[!A-Za-z0-9._-]*)
            die "lane name '$name' from the listing has a character outside [A-Za-z0-9._-] and cannot be quoted safely in the emitted YAML" ;;
    esac
    case "$kind" in
        command|assay) available="$available $name" ;;
        *) : ;;   # a kind this generator was not written against: skipped
    esac
done <<EOF
$listing
EOF
[ -n "$available" ] || die "'$project/run-gate.py --list' listed no lane of kind \"command\" or \"assay\" — nothing to run remotely"

# --- selection -------------------------------------------------------------
requested=${RUN_GATE_LANES-}
# Globbing OFF for the deliberate word-splits below: RUN_GATE_LANES='*' would
# otherwise expand against the current directory and turn stray filenames into
# lane names (it fails closed today — a file is not a lane — but the class of
# bug is removable, so remove it).
set -f
if [ -z "${requested//[[:space:]]/}" ]; then
    selected=$available            # every lane the listing shows (see header)
else
    selected=""
    unknown=""
    duplicate=""
    seen=""
    for want in $requested; do
        found=""
        for have in $available; do
            if [ "$want" = "$have" ]; then
                found=yes
                break
            fi
        done
        # Duplicates are judged over EVERY requested name, not just the ones
        # that resolved: "nope nope" is one unknown name asked for twice, and
        # listing it twice in the refusal reads like two different mistakes.
        case " $seen " in
            *" $want "*) duplicate="$duplicate $want" ;;
            *) seen="$seen $want"
               if [ -n "$found" ]; then
                   selected="$selected $want"
               else
                   unknown="$unknown $want"
               fi ;;
        esac
    done
    if [ -n "$unknown" ]; then
        die "RUN_GATE_LANES names lane(s)$unknown that '$project/run-gate.py --list' does not show; it shows:$available"
    fi
    if [ -n "$duplicate" ]; then
        die "RUN_GATE_LANES names lane(s)$duplicate more than once; that would emit two identical steps with the same label, which is never what was meant"
    fi
fi

# --- emit ------------------------------------------------------------------
# Every key below is the §3 step shape, in the manual's order.
printf 'steps:\n'
for lane in $selected; do
    printf '  - label: "run-gate: %s on %s"\n' "$lane" "$queue"
    printf '    command: "%s./run-gate.py %s"\n' "$cd_prefix" "$lane"
    printf '    agents:\n'
    printf '      queue: "%s"\n' "$queue"
    printf '    concurrency: 1\n'
    printf '    concurrency_group: "gate/%s"\n' "$queue"
    printf '    timeout_in_minutes: %s\n' "$timeout"
    printf '    artifact_paths:\n'
    # Three FIXED globs — the generator cannot see a lane's declared
    # `artifacts` (--list does not carry them, and reading run-gate.toml is the
    # forbidden second parser), so an artifact a lane declares OUTSIDE these
    # prefixes does not travel. `.assay/*` is deliberate insurance beside
    # `.assay/**/*`: whether `**` also matches a file sitting directly in
    # `.assay/` (the progress file does) is Buildkite's zglob's business, and
    # that has not been observed on a live build yet.
    printf '      - "%s.assay/*"\n' "$art_prefix"
    printf '      - "%s.assay/**/*"\n' "$art_prefix"
    printf '      - "%s.run-gate/history.json"\n' "$art_prefix"
    printf '    env:\n'
    printf '      RUN_GATE_LANE: "%s"\n' "$lane"
done
