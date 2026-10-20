#!/usr/bin/env bash
# Host-side driver for the zswap writeback-governor synthetic.
#
# Runs ONLY in the QEMU/TCG VM harness (scripts/debian-install-v2/testing/vm), never on
# the live host: every swapon, sysctl and memory hog happens inside a guest that has its
# own kernel. The device-level write throttle is applied with QEMU's
# block_set_io_throttle over the run's QMP socket, from inside the harness's runner
# container (no host privilege involved).
#
#   run-synthetic.sh prepare                 one-time: build the 7.x-kernel guest base image "zwg-ready"
#   run-synthetic.sh run <variant> <rep>     one run, results into $RESULTS/<variant>-r<rep>/
#   run-synthetic.sh matrix                  the whole matrix of ../README.md (long; the controller grants the slot)
#
# Prerequisite (once): ./run-vm-harness.sh prepare-ready-base case-b
#
# Env: VM_DIR, RESULTS, MEM_MB (3072), SMP (2), DISK_GB (12), THROTTLE_MIBPS (4),
#      THROTTLE_DEVICE (virtio0), CAP_S, WS_MB ... (the last ones pass through to the guest).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VM_DIR="${VM_DIR:-$REPO/scripts/debian-install-v2/testing/vm}"
RESULTS="${RESULTS:-$HERE/results/$(date -u +%Y%m%dT%H%M%SZ)}"
MEM_MB="${MEM_MB:-3072}"
SMP="${SMP:-2}"
DISK_GB="${DISK_GB:-12}"
THROTTLE_MIBPS="${THROTTLE_MIBPS:-4}"
THROTTLE_DEVICE="${THROTTLE_DEVICE:-virtio0}"
GUEST_PASS_ENV="WS_MB HOT_PCT COLD_PUSHED_PCT CHURN_MB CHURN_PERIOD_S GAME_CHURN_MB CAP_S FILL_TIMEOUT_S STALL_MS"

die() { echo "run-synthetic: $*" >&2; exit 1; }
vm() { "$VM_DIR/run-vm-harness.sh" "$@"; }

[ -x "$VM_DIR/run-vm-harness.sh" ] || die "harness not found at $VM_DIR"

runner_name() {
    vm --daemon | sed -n 's/^runner ready: docker exec -it \([^ ]*\) bash$/\1/p'
}

qmp() {  # qmp <run> '<json body>'  -> prints the reply
    local run="$1" body="$2" runner state
    runner="$(runner_name)"; [ -n "$runner" ] || die "cannot determine the runner container name"
    state="$(docker exec "$runner" printenv MDT_VM_STATE_DIR)"; [ -n "$state" ] || die "runner has no MDT_VM_STATE_DIR"
    docker exec -i "$runner" bash -c \
        "printf '{\"execute\":\"qmp_capabilities\"}\n%s\n' '$body' | timeout 10 socat -t2 - UNIX-CONNECT:$state/runs/$run/qmp.sock"
}

set_throttle() {  # set_throttle <run> <bytes-per-second|0>
    local run="$1" bps="$2" reply
    reply="$(qmp "$run" '{"execute":"query-block"}')"
    echo "$reply" | grep -q "\"device\": *\"$THROTTLE_DEVICE\"" \
        || die "QEMU reports no block device named '$THROTTLE_DEVICE' (query-block: ${reply:0:300}); set THROTTLE_DEVICE"
    local args="\"bps\":0,\"bps_rd\":0,\"bps_wr\":$bps,\"iops\":0,\"iops_rd\":0,\"iops_wr\":0"
    reply="$(qmp "$run" "{\"execute\":\"block_set_io_throttle\",\"arguments\":{\"id\":\"$THROTTLE_DEVICE\",$args}}")"
    if echo "$reply" | grep -q '"error"'; then   # older QEMU spells the key "device"
        reply="$(qmp "$run" "{\"execute\":\"block_set_io_throttle\",\"arguments\":{\"device\":\"$THROTTLE_DEVICE\",$args}}")"
    fi
    echo "$reply" | grep -q '"error"' && die "block_set_io_throttle rejected: $reply"
    echo "+ throttle $THROTTLE_DEVICE bps_wr=$bps applied" >&2
}

guest() { vm ssh "$1" -- "${@:2}"; }

copy_guest_files() {  # copy_guest_files <run>
    guest "$1" 'rm -rf /tmp/zwg && mkdir -p /tmp/zwg'
    vm copy "$1" "$HERE/guest" /tmp/zwg/guest
}

cmd_prepare() {
    local run=zwgprep
    vm start "$run" --case case-b-ready --mem "$MEM_MB" --smp "$SMP" --disk-gb "$DISK_GB"
    vm wait "$run"
    copy_guest_files "$run"
    set +e
    guest "$run" 'sudo bash /tmp/zwg/guest/ensure-kernel.sh'
    local rc=$?
    set -e
    if [ "$rc" = 10 ]; then
        guest "$run" 'sudo systemctl reboot' || true
        sleep 20
        vm wait "$run" --timeout-s 900
    elif [ "$rc" != 0 ]; then
        die "ensure-kernel failed with $rc"
    fi
    local kr; kr="$(guest "$run" 'uname -r' | tr -d '\r')"
    case "$kr" in
        [7-9].*|[1-9][0-9].*) ;;
        *) die "guest kernel after prepare is '$kr', not 7.x; backports does not carry one for this suite yet" ;;
    esac
    echo "+ guest kernel $kr" >&2
    vm stop "$run"
    vm snapshot "$run" zwg-ready
    vm destroy "$run"
    echo "prepared base image case 'zwg-ready' (kernel $kr)"
}

cmd_run() {
    local variant="${1:?variant}" rep="${2:?rep}"
    local run="zwg-${variant}-r${rep}" out="$RESULTS/${variant}-r${rep}"
    mkdir -p "$out"
    trap 'vm destroy "'"$run"'" >/dev/null 2>&1 || true' EXIT
    vm start "$run" --case zwg-ready --mem "$MEM_MB" --smp "$SMP" --disk-gb "$DISK_GB"
    vm wait "$run"
    copy_guest_files "$run"
    guest "$run" 'sudo bash /tmp/zwg/guest/setup-guest.sh'

    # Prove the throttle works before trusting any timing: a direct 32 MiB write must run at the target rate.
    local bps=$(( THROTTLE_MIBPS * 1048576 ))
    set_throttle "$run" "$bps"
    local mbps
    mbps="$(guest "$run" 'sudo dd if=/dev/zero of=/var/tmp/zwg-thr bs=1M count=32 oflag=direct 2>&1 | awk "/copied/ {print \$(NF-1)}"; sudo rm -f /var/tmp/zwg-thr' | head -1 | tr -d '\r')"
    awk -v m="$mbps" -v t="$THROTTLE_MIBPS" 'BEGIN { exit !(m+0 >= 0.65*t*1.048576 && m+0 <= 1.35*t*1.048576) }' \
        || die "throttle check failed: dd measured '$mbps' MB/s, expected about $(awk -v t="$THROTTLE_MIBPS" 'BEGIN{print t*1.048576}') MB/s"

    local envs="THROTTLE_MIBPS=$THROTTLE_MIBPS" v
    for v in $GUEST_PASS_ENV; do
        [ -n "${!v:-}" ] && envs="$envs $v=${!v}"
    done
    guest "$run" "sudo env $envs bash /tmp/zwg/guest/run-variant.sh $variant /tmp/zwg-out"
    guest "$run" 'sudo tar -C /tmp/zwg-out -cf - .' > "$out/guest-out.tar"
    tar -C "$out" -xf "$out/guest-out.tar" && rm -f "$out/guest-out.tar"
    guest "$run" 'uname -a; sudo sysctl vm.watermark_scale_factor vm.min_free_kbytes vm.swappiness' > "$out/guest-env.txt" || true
    echo "results in $out"
}

cmd_matrix() {
    # Order puts the cheap controls first so a broken setup fails early.
    local plan=("noshrink 1" "idle 1" "highonly-noshrink 1"
                "base 1" "base 2" "base 3"
                "squeeze 1" "squeeze 2" "squeeze 3"
                "squeeze-minlow 1" "squeeze-minlow 2" "squeeze-minlow 3"
                "wsf200 1" "wsf200 2" "wsf200 3"
                "highonly 1" "highonly 2" "highonly 3")
    local p
    mkdir -p "$RESULTS"
    for p in "${plan[@]}"; do
        # shellcheck disable=SC2086
        ( cmd_run $p ) || echo "run-synthetic: run '$p' FAILED (kept going; analyze.py reports it as invalid)" >&2
    done
    python3 "$HERE/analyze.py" "$RESULTS"
}

case "${1:-}" in
    prepare) cmd_prepare ;;
    run) shift; cmd_run "$@" ;;
    matrix) cmd_matrix ;;
    *) sed -n '2,16p' "$0"; exit 2 ;;
esac
