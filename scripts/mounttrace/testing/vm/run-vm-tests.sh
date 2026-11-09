#!/usr/bin/env bash
# Run mounttrace's test suite inside a QEMU/TCG guest: its own kernel, so the
# root-only scenarios (real btrfs on a loop device, Docker bind propagation)
# never touch this host's mount table, loop devices or Docker.
#
# Uses the shared harness scripts/debian-install-v2/testing/vm (vmctl) with
# MDT_VM_PROJECT_DIR=scripts/mounttrace, so all VM state lives in THIS
# project's artifacts/vm-lab (an ignored symlink into the btrfs CoW area:
# run setup-vm-lab.sh once).
#
# Saved state / rollback: the first run builds the sealed base image
# "mounttrace-ready-v2" (Debian 13 cloud image + cloud-init done + btrfs-progs,
# docker.io, a local busybox image). Every test round boots a fresh qcow2
# overlay on that base and destroys it afterwards, so each round starts from
# the identical saved state; nothing a test does survives.
#
# Usage: run-vm-tests.sh [--keep] [--rebuild-base] [--base-only] [--smp N] [--mem MB]
#   --keep          leave the test VM running (inspect it with the harness ssh)
#   --base-only     only make sure the sealed base exists
#   --rebuild-base  delete and rebuild mounttrace-ready-v2 (no run may use it)
set -euo pipefail

die() { echo "run-vm-tests: $*" >&2; exit 1; }
log() { echo "== $*" >&2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd "$HERE/../.." && pwd -P)"
REPO_ROOT="$(cd "$PROJECT_DIR/../.." && pwd -P)"
HARNESS="$REPO_ROOT/scripts/debian-install-v2/testing/vm/run-vm-harness.sh"
export MDT_VM_PROJECT_DIR="$PROJECT_DIR"
VM_LAB="$PROJECT_DIR/artifacts/vm-lab"
BASE_CASE=mounttrace-ready-v2
GUEST_DIR=/home/tester/mounttrace

KEEP=0; REBUILD=0; BASE_ONLY=0; SMP=4; MEM=3072
CHECK_INPUTS=0
INPUT_MANIFEST=""
while [ $# -gt 0 ]; do
    case "$1" in
        --keep) KEEP=1; shift ;;
        --base-only) BASE_ONLY=1; shift ;;
        --rebuild-base) REBUILD=1; shift ;;
        --check-inputs-only) CHECK_INPUTS=1; shift ;;
        --input-manifest) INPUT_MANIFEST="${2:?}"; shift 2 ;;
        --smp) SMP="${2:?}"; shift 2 ;;
        --mem) MEM="${2:?}"; shift 2 ;;
        *) die "unknown option $1" ;;
    esac
done

TEST_INPUTS_DEFAULT="$PROJECT_DIR/tests/test-inputs.txt"
TEST_INPUTS=()
read_test_inputs() {
    local manifest="$1" input parent
    [ -f "$manifest" ] || die "test input manifest missing: $manifest"
    TEST_INPUTS=()
    while IFS= read -r input || [ -n "$input" ]; do
        input="${input%$'\r'}"
        case "$input" in
            ''|\#*) continue ;;
            /*|../*|*/../*|..) die "test input path must stay below the project: $input" ;;
        esac
        [ -f "$PROJECT_DIR/$input" ] || die "test input missing: $PROJECT_DIR/$input"
        TEST_INPUTS+=("$input")
    done < "$manifest"
    [ "${#TEST_INPUTS[@]}" -gt 0 ] || die "test input manifest is empty: $manifest"
}

read_test_inputs "${INPUT_MANIFEST:-$TEST_INPUTS_DEFAULT}"
if [ "$CHECK_INPUTS" = 1 ]; then
    log "test inputs present (${#TEST_INPUTS[@]} files): ${TEST_INPUTS[*]}"
    exit 0
fi

[ -x "$HARNESS" ] || die "shared harness missing: $HARNESS"
[ -d "$VM_LAB" ] || die "$VM_LAB missing; run $HERE/setup-vm-lab.sh first"

# One lane per checkout: the runner's forwarded SSH port is one resource.
LOCK_PATH="/tmp/mdt-mounttrace-vm-$(printf '%s' "$PROJECT_DIR" | sha256sum | cut -c1-12).lock"
exec 9>"$LOCK_PATH"
flock -n 9 || die "another mounttrace VM lane is already running for this checkout"

h() { local t="$1"; shift; timeout --foreground "$t" "$HARNESS" "$@"; }

if [ "$REBUILD" = 1 ] && [ -f "$VM_LAB/images/$BASE_CASE-base.qcow2" ]; then
    log "removing $BASE_CASE (--rebuild-base)"
    rm -f "$VM_LAB/images/$BASE_CASE-base.qcow2"
fi

PREP_RUN="mt-prep-$$"
build_base() {
    local run="$PREP_RUN"
    log "building sealed base $BASE_CASE (one-time; TCG is slow, expect 15-40 min)"
    h 30m prepare-ready-base case-b
    trap 'h 2m destroy "$PREP_RUN" >/dev/null 2>&1 || true' EXIT
    # Debian's cloud image uses HTTPS apt sources; the harness apt cache is HTTP-only.
    h 2m start "$run" --case case-b-ready --no-apt-cache --smp "$SMP" --mem "$MEM"
    h 15m wait "$run"
    h 60m ssh "$run" -- 'set -eu
        export DEBIAN_FRONTEND=noninteractive
        sudo -E apt-get update -q
        sudo -E apt-get install -y -q --no-install-recommends \
            python3 btrfs-progs docker.io docker-cli busybox-static rsync util-linux procps ca-certificates
        sudo systemctl enable --now docker
        nonce=$(cat /proc/sys/kernel/random/uuid)
        test -n "$nonce"
        printf "%s\\n" "$nonce" | sudo tee /etc/mounttrace-test-vm >/dev/null
        sudo chmod 0444 /etc/mounttrace-test-vm
        t=$(mktemp -d); mkdir -p "$t/bin"; cp /bin/busybox "$t/bin/busybox"
        for a in sh sleep cat ls mkdir touch stat; do ln -s busybox "$t/bin/$a"; done
        sudo tar -C "$t" -c . | sudo docker import - mt-busybox:1 >/dev/null
        sudo docker run --rm mt-busybox:1 sh -c "echo busybox-ok"
        sudo apt-get clean
        sync'
    h 5m stop "$run"
    h 20m snapshot "$run" "$BASE_CASE"
    h 2m destroy "$run"
    trap - EXIT
    log "sealed base ready: $VM_LAB/images/$BASE_CASE-base.qcow2"
}

[ -f "$VM_LAB/images/$BASE_CASE-base.qcow2" ] || build_base
[ "$BASE_ONLY" = 0 ] || { log "base present, --base-only: no test round"; exit 0; }

RUN="mt-test-$$"
if [ "$KEEP" = 0 ]; then
    trap 'h 2m destroy "$RUN" >/dev/null 2>&1 || true' EXIT
fi
log "test round $RUN on a fresh overlay of $BASE_CASE"
h 2m start "$RUN" --case "$BASE_CASE" --no-apt-cache --smp "$SMP" --mem "$MEM"
h 15m wait "$RUN"
h 2m ssh "$RUN" -- "mkdir -p $GUEST_DIR"
# /source is this project, mounted read-only in the runner.
h 5m copy "$RUN" /source/tests/ "$GUEST_DIR/tests/"
for input in "${TEST_INPUTS[@]}"; do
    parent="$(dirname "$input")"
    if [ "$parent" != "." ]; then
        h 2m ssh "$RUN" -- "mkdir -p '$GUEST_DIR/$parent'"
    fi
    h 5m copy "$RUN" "/source/$input" "$GUEST_DIR/$input"
done
h 45m ssh "$RUN" -- "set -eu
    vm=\$(systemd-detect-virt --vm 2>/dev/null || true)
    case \"\$vm\" in qemu|kvm) ;; *) echo \"precondition: expected a QEMU/KVM guest, got \${vm:-none}\" >&2; exit 1 ;; esac
    cd $GUEST_DIR
    echo '== unprivileged (user-namespace scenarios)'
    python3 tests/test_mounttrace.py -v
    echo '== root in the guest: btrfs + Docker scenarios REQUIRED (a skip is a failure)'
    # Root-owned copy: root inside the scenarios' child user namespaces has no DAC
    # override on files whose owner (tester) is not mapped there.
    sudo rm -rf /opt/mounttrace && sudo cp -r $GUEST_DIR /opt/mounttrace && sudo chown -R root:root /opt/mounttrace
    sudo env MOUNTTRACE_VM=1 MOUNTTRACE_REQUIRE_VM_TESTS=1 python3 /opt/mounttrace/tests/test_mounttrace.py -v"
log "PASS: all mounttrace tests passed in the guest ($RUN)"
[ "$KEEP" = 1 ] && log "kept: MDT_VM_PROJECT_DIR=$PROJECT_DIR $HARNESS ssh $RUN   (destroy when done)"
exit 0
