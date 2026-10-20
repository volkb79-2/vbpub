#!/usr/bin/env bash
# One-time (per run) guest preparation for the zswap writeback-governor synthetic.
# Runs INSIDE the VM-harness guest as root. NEVER run this on the live host:
# it creates a swap file and swapon's it, and changes vm.* sysctls.
#
# Fails loudly (non-zero) on any missing precondition instead of continuing with
# a default: a synthetic that silently ran on the wrong kernel or without the
# zswap debugfs counters would produce a plausible-looking wrong answer.
set -euo pipefail

SWAPFILE_MB="${SWAPFILE_MB:-4096}"
ZSWAP_COMPRESSOR="${ZSWAP_COMPRESSOR:-zstd}"   # host uses zstd (MEMORY-ARCHITECTURE.md section 3)
ZSWAP_MAX_POOL_PERCENT="${ZSWAP_MAX_POOL_PERCENT:-25}"  # host value
BLOCKDEV="${BLOCKDEV:-vda}"

die() { echo "setup-guest: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "must run as root"

# The guest must be a VM, not a container and not the live host.
if command -v systemd-detect-virt >/dev/null 2>&1; then
    virt="$(systemd-detect-virt --vm || true)"
    case "$virt" in
        qemu|kvm) ;;
        *) die "systemd-detect-virt --vm reports '${virt:-none}', expected qemu/kvm; refusing to touch swap here" ;;
    esac
else
    die "systemd-detect-virt missing; cannot prove this is a QEMU guest"
fi
[ -e "/sys/block/$BLOCKDEV" ] || die "/sys/block/$BLOCKDEV missing"

major="$(uname -r | cut -d. -f1)"
if [ "$major" -lt 7 ] && [ "${ALLOW_OLD_KERNEL:-0}" != 1 ]; then
    die "guest kernel $(uname -r) is older than 7.x; the v7.2 shrinker analysis does not apply. \
Run 'run-synthetic.sh prepare' (installs a trixie-backports kernel) or set ALLOW_OLD_KERNEL=1 \
(the result is then labelled kernel-mismatch in meta.json)"
fi

mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug
[ -d /sys/kernel/debug/zswap ] || die "/sys/kernel/debug/zswap missing: kernel without CONFIG_ZSWAP or debugfs"
for f in pool_total_size stored_pages written_back_pages; do
    [ -r "/sys/kernel/debug/zswap/$f" ] || die "zswap debugfs counter $f unreadable"
done
cat /proc/self/stack >/dev/null 2>&1 || die "/proc/self/stack unreadable (needs root and CONFIG_STACKTRACE); full-stack sampling impossible"

zp=/sys/module/zswap/parameters
echo "$ZSWAP_COMPRESSOR" > "$zp/compressor"
echo "$ZSWAP_MAX_POOL_PERCENT" > "$zp/max_pool_percent"
echo Y > "$zp/shrinker_enabled"
echo Y > "$zp/enabled"
[ "$(cat "$zp/enabled")" = Y ] || die "zswap did not enable"
[ "$(cat "$zp/compressor")" = "$ZSWAP_COMPRESSOR" ] || die "zswap compressor is $(cat "$zp/compressor"), wanted $ZSWAP_COMPRESSOR (module not available?)"
[ "$(cat "$zp/shrinker_enabled")" = Y ] || die "shrinker_enabled did not stick"

# Same on-device queue shape as the host after the B1 fix (IO-STALL-TESTING-GUIDE.md section 10).
echo none > "/sys/block/$BLOCKDEV/queue/scheduler" 2>/dev/null || echo "setup-guest: note: could not set scheduler none" >&2
echo 16 > "/sys/block/$BLOCKDEV/queue/nr_requests" || die "cannot set nr_requests 16"
echo 64 > "/sys/block/$BLOCKDEV/queue/max_sectors_kb" || die "cannot set max_sectors_kb 64"
[ "$(cat "/sys/block/$BLOCKDEV/queue/nr_requests")" = 16 ] || die "nr_requests did not stick (is $(cat "/sys/block/$BLOCKDEV/queue/nr_requests"))"

echo never > /sys/kernel/mm/transparent_hugepage/enabled || die "cannot disable THP"

if [ ! -f /swapfile ]; then
    fallocate -l "${SWAPFILE_MB}M" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
fi
swapoff -a
swapon /swapfile
[ "$(awk 'NR>1' /proc/swaps | wc -l)" = 1 ] || die "expected exactly one active swap area (the guest's /swapfile)"

# cgroup v2 only.
[ "$(stat -fc %T /sys/fs/cgroup)" = cgroup2fs ] || die "/sys/fs/cgroup is not cgroup2"
grep -qw memory /sys/fs/cgroup/cgroup.controllers || die "memory controller not available"
echo +memory > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
mkdir -p /sys/fs/cgroup/zwg
echo +memory > /sys/fs/cgroup/zwg/cgroup.subtree_control
mkdir -p /sys/fs/cgroup/zwg/game /sys/fs/cgroup/zwg/bg
[ -w /sys/fs/cgroup/zwg/game/memory.zswap.writeback ] || die "memory.zswap.writeback missing (kernel < 6.8?)"
echo 1 > /sys/fs/cgroup/zwg/game/memory.zswap.writeback
echo max > /sys/fs/cgroup/zwg/game/memory.zswap.max   # host: memory.zswap.max=max

echo "setup-guest: ok kernel=$(uname -r) zswap compressor=$(cat $zp/compressor) max_pool_percent=$(cat $zp/max_pool_percent) swap=$(awk 'NR==2{print $1,$3}' /proc/swaps)"
