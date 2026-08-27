#!/usr/bin/env bash
# gstammtisch memory-architecture installer.
# Run as root from inside gstammtisch-guide/ ON the target host.
# Copies config files into place, installs scripts, enables units, applies sysctl.
# Does NOT partition the disk or edit GRUB (do those explicitly — see README.md).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # gstammtisch-guide/
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "== host package prerequisites =="
# systemd-oomd: ManagedOOM* slice settings are silent no-ops without it (separate
# package on Debian trixie). vmtouch: pak hot-set measurement.
# jdupes: hardlink-dedupe of duplicate game installs across instance volumes.
apt-get install -y --no-install-recommends systemd-oomd vmtouch jdupes \
  || echo "WARN: prerequisite install failed — install systemd-oomd vmtouch jdupes manually"

echo "== copying config files from $HERE/files into / =="
# Remove the minimal sysctl stub deployed by the initial commit; the full
# documented version (99-gstammtisch-memory.conf) supersedes it.
rm -f /etc/sysctl.d/99-memory.conf
# /etc/docker/daemon.json is NOT copied from here (host dev-tier cgroup
# governance rollout): it now has one owner, mdt's host-setup/ companion
# (modern-debian-tools-python-debug/host-setup/install.sh) — see that
# script's "docker daemon.json" step. Two installers each doing a wholesale
# copy of this file would silently fight over it, last-writer-wins.
# cp -r WITHOUT -a/--preserve: -a copies the repo checkout's user ownership and
# group-writable modes onto the TARGET DIRECTORIES themselves (observed
# 2026-07-07: /etc became vb:vb 775). Plain -r as root creates root-owned
# files with umask modes, which is what system config must be.
cp -rv "$HERE/files/etc/."            /etc/
cp -rv "$HERE/files/usr/local/sbin/." /usr/local/sbin/
chmod +x /usr/local/sbin/soulmask-shutdown.sh \
         /usr/local/sbin/soulmask-pak-ramdisk-setup.sh /usr/local/sbin/soulmask-pak-ramdisk-toggle.sh \
         /usr/local/sbin/soulmask-pak-ramdisk-teardown.sh \
         /usr/local/sbin/soulmask-monitor.sh /usr/local/sbin/soulmask-monitor.py \
         /usr/local/sbin/soulmask-mempress.sh /usr/local/sbin/soulmask-pak-mempress.sh \
         /usr/local/sbin/container-mempress.sh
# soulmask-instance-lib.sh is sourced only (not directly executed) — no +x needed.
# setup-cgroups.sh / soulmask-cgroup-watcher.sh / soulmask-startup-cgroup.sh are
# RETIRED (see files/legacy/) — superseded by patched Wings' own native
# per-server slice placement + staged startup/steady bands; no longer copied
# here, so no longer chmod'd or enabled below.
# /etc/gstammtisch/instance-defaults.env + instances.d/*.env came in via the
# etc/ copy above — see SOULMASK.md "Multi-instance operations".

echo "== BFQ I/O scheduler =="
# BFQ is required for cgroup io.weight / io.bfq.weight to have any effect.
# Without it, [none] scheduler ignores all I/O priority settings.
modprobe bfq && echo "bfq loaded" || echo "WARN: modprobe bfq failed"
echo bfq > /sys/block/vda/queue/scheduler 2>/dev/null && \
  echo "vda scheduler → bfq" || echo "WARN: could not set vda scheduler"
udevadm control --reload-rules && udevadm trigger --action=change \
  --subsystem-match=block 2>/dev/null && echo "udev rules reloaded" || true

echo "== installing scripts =="
# exec-soulmask-rcon.sh/.py are installed by the files/usr/local/sbin/ copy
# above, not here — this line used to point at a $HERE/scripts/ copy of the
# script that no longer exists (it moved into files/usr/local/sbin/ with the
# soulmask_rcon.py split), so it always failed `install` and aborted the
# script under `set -e`. Left removed rather than restored.
# moved to `debian-install`
#install -m 0755 "$HERE/scripts/partition-editor.py"   /usr/local/sbin/partition-editor.py
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
systemctl enable soulmask-paks.slice 2>/dev/null || true   # pak ramdisk cgroup slice
# The dev tiers (dev-interactive.slice / dev-background.slice) are NOT
# installed here — they belong to mdt host-setup, see the note at the end of
# this script. gstammtisch-cgroups.service / soulmask-cgroup-watcher.service
# are RETIRED (files/legacy/) — patched Wings now places+configures each
# Soulmask container's cgroup natively; nothing here enables them anymore.
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
  Note: partition-editor.py moved to `debian-install` and was renamed
        inuse-partition-editor.py (it edits a disk that's currently in use)
  1) Create swap partitions (dry-run, then --commit):
       inuse-partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2
       inuse-partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2 --commit
  2) GRUB: ensure GRUB_CMDLINE_LINUX has NO zswap.* tokens (handled post-boot now);
     optionally add `preempt=full` for lower game-tick latency. update-grub if changed.
  3) Per-server memory floors/weights are set via patched Wings itself now
     (docker.per_server_slices config + admin-only WINGS_CG_* egg overrides —
     see the wings-cgroups project's SETUP.md), not gstammtisch — that
     retrofit path (setup-cgroups.sh, SOULMASK_MIN in
     /etc/gstammtisch/instances.d/<server-uuid>.env) is retired.
  4) Pterodactyl panel: set Soulmask memory/CPU/IO limits.
  5) Pre-pull the RCON image:  docker pull itzg/rcon-cli
     Verify RCON:               exec-soulmask-rcon.sh -d List_OnlinePlayers
  6) Watch health:              swap-health watch
  7) Dev/test/build containers on this host are governed SEPARATELY, by the mdt
     host-setup companion (dev-interactive.slice + dev-background.slice,
     measured IO caps, the fio baseline, BFQ setup, AND /etc/docker/daemon.json —
     the sole owner of that file now, see the note above):
       sudo <vbpub>/modern-debian-tools-python-debug/host-setup/install.sh --with-baseline
       sudo mdt-host-check.sh
     Run the baseline while the game is STOPPED — it saturates the disk ~4 min.
NEXT
