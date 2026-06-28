#!/usr/bin/env bash
# gstammtisch memory-architecture installer.
# Run as root from inside gstammtisch-guide/ ON the target host.
# Copies config files into place, installs scripts, enables units, applies sysctl.
# Does NOT partition the disk or edit GRUB (do those explicitly — see README.md).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # gstammtisch-guide/
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "== copying config files from $HERE/files into / =="
# Remove the minimal sysctl stub deployed by the initial commit; the full
# documented version (99-gstammtisch-memory.conf) supersedes it.
rm -f /etc/sysctl.d/99-memory.conf
cp -av "$HERE/files/etc/."            /etc/
cp -av "$HERE/files/usr/local/sbin/." /usr/local/sbin/
chmod +x /usr/local/sbin/setup-cgroups.sh /usr/local/sbin/soulmask-shutdown.sh \
         /usr/local/sbin/soulmask-pak-ramdisk-setup.sh /usr/local/sbin/soulmask-pak-ramdisk-toggle.sh \
         /usr/local/sbin/soulmask-zswap-monitor.sh /usr/local/sbin/soulmask-mempress.sh \
         /usr/local/sbin/soulmask-startup-cgroup.sh /usr/local/sbin/soulmask-pak-mempress.sh

echo "== BFQ I/O scheduler =="
# BFQ is required for cgroup io.weight / io.bfq.weight to have any effect.
# Without it, [none] scheduler ignores all I/O priority settings.
modprobe bfq && echo "bfq loaded" || echo "WARN: modprobe bfq failed"
echo bfq > /sys/block/vda/queue/scheduler 2>/dev/null && \
  echo "vda scheduler → bfq" || echo "WARN: could not set vda scheduler"
udevadm control --reload-rules && udevadm trigger --action=change \
  --subsystem-match=block 2>/dev/null && echo "udev rules reloaded" || true

echo "== installing scripts =="
install -m 0755 "$HERE/scripts/exec-soulmask-rcon.sh" /usr/local/sbin/exec-soulmask-rcon.sh
install -m 0755 "$HERE/scripts/partition-editor.py"   /usr/local/sbin/partition-editor.py
install -m 0755 "$HERE/scripts/swap-health.sh"        /usr/local/bin/swap-health

echo "== sysctl =="
sysctl --system >/dev/null

echo "== tmpfiles (THP; KSM if you kept ksm.conf) =="
# w! entries (THP, KSM) are boot-only — skipped by --create, applied on next boot
# via systemd-tmpfiles-setup.service (--boot).  Apply sysfs writes directly now too.
systemd-tmpfiles --create || true
echo madvise > /sys/kernel/mm/transparent_hugepage/enabled  2>/dev/null || true
echo madvise > /sys/kernel/mm/transparent_hugepage/defrag   2>/dev/null || true

echo "== systemd: reload + enable units =="
systemctl daemon-reload
systemctl enable --now zswap-config.service
systemctl enable dev-workloads.slice 2>/dev/null || true
systemctl enable soulmask-paks.slice 2>/dev/null || true   # pak ramdisk cgroup slice
systemctl enable --now gstammtisch-cgroups.service
systemctl enable --now soulmask-graceful-stop.service
systemctl enable --now systemd-oomd.service 2>/dev/null || true

echo ""
echo "== pak ramdisk (opt-in — see SOULMASK.md §2c) =="
echo "   To eliminate pak page-fault stalls, enable the pak ramdisk:"
echo "     sudo /usr/local/sbin/soulmask-pak-ramdisk-setup.sh --dry-run   # preview"
echo "     sudo systemctl enable --now soulmask-pak-ramdisk.service"
echo "   (stop the server first; verify with: findmnt .../WS/Content/Paks)"

echo; echo "== status =="
echo "zswap compressor: $(cat /sys/module/zswap/parameters/compressor 2>/dev/null) (want: zstd)"
swapon --show 2>/dev/null || echo "(no swap yet — create partitions, step 1 below)"

cat <<'NEXT'

== NEXT (manual — see README.md) ==
  1) Create swap partitions (dry-run, then --commit):
       partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2
       partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2 --commit
  2) GRUB: ensure GRUB_CMDLINE_LINUX has NO zswap.* tokens (handled post-boot now);
     optionally add `preempt=full` for lower game-tick latency. update-grub if changed.
  3) Measure Soulmask hot set with DAMON, set SOULMASK_MIN in
     /usr/local/sbin/setup-cgroups.sh, then: systemctl restart gstammtisch-cgroups
  4) Pterodactyl panel: set Soulmask memory/CPU/IO limits.
     Launch dev containers with:  --cgroup-parent=dev-workloads.slice --label workload=dev
  5) Pre-pull the RCON image:  docker pull itzg/rcon-cli
     Verify RCON:               exec-soulmask-rcon.sh -d List_OnlinePlayers
  6) Watch health:              swap-health watch
NEXT
