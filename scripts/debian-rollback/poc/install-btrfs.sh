#!/usr/bin/env bash
# PoC installer: Debian 13 on btrfs with one writable subvolume per OS state.
# Run INSIDE a disposable guest (or the future installer ISO), never on a
# machine whose disk matters. It wipes the device you name.
#
#   install-btrfs.sh --device /dev/vdb --yes [--bootloader grub|systemd-boot]
#                    [--suite trixie] [--mirror URL] [--security-mirror URL]
#                    [--hostname NAME] [--root-password PW] [--dry-run]
#
# Layout (flat, default subvolume stays the top level, id 5):
#   p1 ESP (vfat) at /boot/efi      p2 btrfs "drroot"
#   @os-000001-YYYYMMDD-HHMMSSZ-install  the initial OS state (root, /boot, /etc, /opt, ...)
#   @home @root @srv @www @log   outside the state (D18)
#   @docker @containers @postgresql @mysql @usr-local
# Kernels live inside each state. GRUB (the default) reads them directly from a
# static menu on the ESP (design guide F5). systemd-boot cannot read btrfs, so
# dr-state copies the newest bootable pair per state to a deduplicated ESP pool.
set -euo pipefail

DEVICE="" ; YES=0 ; DRY=0 ; BOOTLOADER=grub
SUITE=trixie ; MIRROR=http://deb.debian.org/debian ; SECURITY_MIRROR=http://deb.debian.org/debian-security
HOSTNAME_=drpoc ; ROOTPW=root ; ROOTPW_HASH=""
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "install-btrfs: $*" >&2; exit 1; }
run() { echo "+ $*" >&2; [ "$DRY" = 1 ] || "$@"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --device) DEVICE=${2:?}; shift 2 ;;
    --yes) YES=1; shift ;;
    --suite) SUITE=${2:?}; shift 2 ;;
    --mirror) MIRROR=${2:?}; shift 2 ;;
    --security-mirror) SECURITY_MIRROR=${2:?}; shift 2 ;;
    --hostname) HOSTNAME_=${2:?}; shift 2 ;;
    --root-password) ROOTPW=${2:?}; shift 2 ;;
    --root-password-hash) ROOTPW_HASH=${2:?}; shift 2 ;;   # crypt(3) hash; replaces --root-password
    --bootloader) BOOTLOADER=${2:?}; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$DEVICE" ] || die "--device is required"
[ "$YES" = 1 ] || die "refusing to wipe $DEVICE without --yes"
case "$BOOTLOADER" in grub|systemd-boot) ;; *) die "--bootloader must be grub or systemd-boot" ;; esac
[ "$DRY" = 1 ] || [ -b "$DEVICE" ] || die "$DEVICE is not a block device"
[ -d /sys/firmware/efi ] || [ "$DRY" = 1 ] || die "not booted in UEFI mode (D4: UEFI only)"
if [ "$DRY" != 1 ]; then
  # Fail closed: every check must positively succeed before anything is wiped.
  DEVICE=$(readlink -f "$DEVICE") || die "cannot resolve $DEVICE"
  [ "$(lsblk -dno TYPE "$DEVICE")" = disk ] || die "$DEVICE is not a whole disk"
  mounts=$(lsblk -nro MOUNTPOINTS "$DEVICE") || die "lsblk failed on $DEVICE; refusing"
  [ -z "${mounts//[[:space:]]/}" ] || die "$DEVICE has mounted filesystems; refusing"
  swaps=$(swapon --noheadings --show=NAME) || die "swapon failed; refusing"
  case "$swaps" in *"$DEVICE"*) die "$DEVICE is in use as swap; refusing" ;; esac
  rootsrc=$(findmnt -no SOURCE /) || die "findmnt / failed; refusing"
  case "$rootsrc" in "$DEVICE"*) die "$DEVICE holds the running root; refusing" ;; esac
fi

part() { case "$DEVICE" in *[0-9]) echo "${DEVICE}p$1" ;; *) echo "${DEVICE}$1" ;; esac; }
ESP=$(part 1) ; ROOT=$(part 2)
T=/mnt/drtarget ; TOP=/mnt/drtop
cleanup() { # a failed run must not leave the target mounted (the next run's guard would refuse)
  local rc=$?
  [ "$DRY" = 1 ] && return 0
  if [ "$rc" != 0 ] && [ -s "$T/debootstrap/debootstrap.log" ]; then
    echo "install-btrfs: failed (rc $rc); tail of debootstrap.log:" >&2
    tail -n 60 "$T/debootstrap/debootstrap.log" >&2 || true
  fi
  umount -R "$T" 2>/dev/null || true
  umount "$TOP" 2>/dev/null || true
}
trap cleanup EXIT
STATE_TIMESTAMP=$(date -u +%Y%m%d%H%M%S)
STATE=$("$HERE/dr-state" _state-name 000001 "$STATE_TIMESTAMP" install) \
  || die "cannot construct the timestamped initial state ID"
DATA_SUBVOLS="@home:/home @root:/root @srv:/srv @www:/var/www @log:/var/log @docker:/var/lib/docker @containers:/var/lib/containers @postgresql:/var/lib/postgresql @mysql:/var/lib/mysql @usr-local:/usr/local"
# the database and container directories must not be copy-on-write
NOCOW="@docker @containers @postgresql @mysql"

run sgdisk --zap-all "$DEVICE"
run sgdisk -n1:0:+512M -t1:ef00 -c1:ESP -n2:0:0 -t2:8300 -c2:drroot "$DEVICE"
run partprobe "$DEVICE"
run udevadm settle
run mkfs.vfat -F32 -n ESP "$ESP"
run mkfs.btrfs -f -L drroot "$ROOT"

run mkdir -p "$TOP" "$T"
run mount "$ROOT" "$TOP"
run btrfs subvolume create "$TOP/$STATE"
for sv in $DATA_SUBVOLS; do
  name=${sv%%:*}
  run btrfs subvolume create "$TOP/$name"
  case " $NOCOW " in *" $name "*) run chattr +C "$TOP/$name" ;; esac
done

run mount -o "subvol=$STATE" "$ROOT" "$T"
for sv in $DATA_SUBVOLS; do
  name=${sv%%:*} ; path=${sv##*:}
  run mkdir -p "$T$path"
  run mount -o "subvol=$name" "$ROOT" "$T$path"
done
run mkdir -p "$T/boot/efi" "$T/.top"
run mount "$ESP" "$T/boot/efi"

BOOT_PACKAGES=grub-efi-amd64
[ "$BOOTLOADER" = systemd-boot ] && BOOT_PACKAGES=systemd-boot,systemd-boot-efi
run debootstrap --include=linux-image-amd64,"$BOOT_PACKAGES",btrfs-progs,snapper,initramfs-tools,systemd-sysv,openssh-server,sudo,locales,ifupdown,isc-dhcp-client,dosfstools,ca-certificates "$SUITE" "$T" "$MIRROR"

if [ "$DRY" != 1 ]; then
  RUUID=$(blkid -s UUID -o value "$ROOT")
  EUUID=$(blkid -s UUID -o value "$ESP")
else
  RUUID="ROOT-UUID" ; EUUID="ESP-UUID"
fi

# one option set for every mount of the filesystem: btrfs keeps mount options per superblock
OPTS=defaults,noatime,compress=zstd:1
fstab=$(
  echo "# generated by install-btrfs.sh; root is chosen by the boot menu (rootflags=subvol=)"
  echo "UUID=$RUUID / btrfs $OPTS 0 0"
  for sv in $DATA_SUBVOLS; do
    name=${sv%%:*} ; path=${sv##*:}
    echo "UUID=$RUUID $path btrfs $OPTS,subvol=$name 0 0"
  done
  echo "UUID=$RUUID /.top btrfs $OPTS,subvolid=5 0 0"
  echo "UUID=$EUUID /boot/efi vfat umask=0077 0 1"
)
write_file() { # write_file PATH (content on stdin); shows it instead in a dry run
  if [ "$DRY" = 1 ]; then cat; else cat >"$1"; fi
}
echo "$fstab" | write_file "$T/etc/fstab"
echo "$HOSTNAME_" | write_file "$T/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 %s\n' "$HOSTNAME_" | write_file "$T/etc/hosts"
# security and point-release updates: debootstrap configures only the base suite, so a fresh install would miss
# kernel and security updates (found in run 2: the target had kernel 6.12.107 while the mirror offered newer ones)
printf 'deb %s %s-updates main\ndeb %s %s-security main\n' "$MIRROR" "$SUITE" "$SECURITY_MIRROR" "$SUITE" \
  | write_file "$T/etc/apt/sources.list.d/dr-updates.list"
# DHCP on the first NIC through ifupdown + isc-dhcp-client (dhclient writes resolv.conf). The menu passes
# net.ifnames=0 so the NIC is always eth0. (systemd-resolved failed to configure inside the debootstrap chroot
# in run 2a, see RESULTS; networkd/resolved is the better product answer and stays an open item.)
run mkdir -p "$T/etc/network/interfaces.d"
printf 'allow-hotplug eth0\niface eth0 inet dhcp\n' | write_file "$T/etc/network/interfaces.d/eth0"

run install -m 0755 "$HERE/dr-state" "$T/usr/local/sbin/dr-state"
run mount --bind /dev "$T/dev"
run mount --bind /proc "$T/proc"
run mount --bind /sys "$T/sys"
run mount --bind /sys/firmware/efi/efivars "$T/sys/firmware/efi/efivars" || true

chroot_run() { run chroot "$T" "$@"; }
if [ "$DRY" != 1 ]; then
  if [ -n "$ROOTPW_HASH" ]; then echo "root:$ROOTPW_HASH" | chroot "$T" chpasswd -e; else echo "root:$ROOTPW" | chroot "$T" chpasswd; fi
fi
run chmod 700 "$T/root"
chroot_run update-initramfs -u -k all
if [ "$BOOTLOADER" = grub ]; then
  chroot_run /usr/local/sbin/dr-state install-grub
else
  # Do not depend on writable EFI variables while installing. The state-carrying config and the ESP marker both
  # select systemd-boot; dr-state fails loudly if they later disagree.
  chroot_run bootctl install --esp-path=/boot/efi --no-variables
  # Debian's systemd-boot kernel hooks call kernel-install, which creates an extra machine-id BLS entry and kernel
  # copy outside dr-state's per-state ESP layout. Divert those hooks; the zz-dr-state hooks below own kernel updates.
  # Keep diverted files outside the run-parts directories: --lsbsysinit accepts the usual .distrib suffix.
  chroot_run mkdir -p /etc/kernel/dr-disabled
  for d in postinst.d postrm.d; do
    hook="/etc/kernel/$d/zz-systemd-boot"
    target="/etc/kernel/dr-disabled/${d%.d}-zz-systemd-boot"
    chroot_run dpkg-divert --local --add --rename --divert "$target" "$hook"
  done
  # The systemd-boot package postinst calls kernel-install directly, bypassing the hooks above. Disable only its
  # stock BLS writer through systemd's supported /etc override; dr-state owns the per-state ESP entries.
  chroot_run mkdir -p /etc/kernel/install.d
  chroot_run ln -s /dev/null /etc/kernel/install.d/90-loaderentry.install
  run mkdir -p "$T/boot/efi/EFI/dr"
  printf 'systemd-boot\n' | write_file "$T/boot/efi/EFI/dr/bootloader"
  printf 'BOOTLOADER=systemd-boot\n' | write_file "$T/etc/dr-state.conf"
fi
chroot_run /usr/local/sbin/dr-state initialize-state "$STATE" "$STATE_TIMESTAMP"
chroot_run /usr/local/sbin/dr-state gen-menu --default "$STATE"

# Hooks. apt: one writable copy of the running state before every dpkg run, menu refreshed after it.
# kernel: the menu follows kernel installs and removals (a menu naming a removed kernel does not boot).
# DR_STATE=none in the environment skips the snapshot (recovery chroots have no rootflags= on the command line).
if [ "$DRY" != 1 ]; then
  cat >"$T/etc/apt/apt.conf.d/80dr-state" <<'EOF'
DPkg::Tools::Options::/usr/local/sbin/dr-state::Version "2";
DPkg::Tools::Options::/usr/local/sbin/dr-state::InfoFD "3";
DPkg::Pre-Install-Pkgs { "/usr/local/sbin/dr-state apt-pre-install-pkgs"; };
DPkg::Pre-Invoke { "[ ! -x /usr/local/sbin/dr-state ] || /usr/local/sbin/dr-state snapshot --reason apt || exit 100"; };
DPkg::Post-Invoke { "[ ! -x /usr/local/sbin/dr-state ] || /usr/local/sbin/dr-state gen-menu || true"; };
EOF
  for d in postinst.d postrm.d; do
    mkdir -p "$T/etc/kernel/$d"
    cat >"$T/etc/kernel/$d/zz-dr-state" <<'EOF'
#!/bin/sh
# regenerate the ESP boot menu after a kernel is installed or removed
[ -x /usr/local/sbin/dr-state ] || exit 0
/usr/local/sbin/dr-state gen-menu || echo "dr-state: gen-menu failed, boot menu not refreshed" >&2
exit 0
EOF
    chmod 755 "$T/etc/kernel/$d/zz-dr-state"
  done
fi

run rm -f "$T/run/dr-state.lock"   # the lock file must not be carried into the state (and its snapshots)
run umount -R "$T"
run umount "$TOP"
trap - EXIT
echo "install-btrfs: done. Shut the guest down and boot from $DEVICE alone."
