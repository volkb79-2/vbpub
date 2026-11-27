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
if [ -d "$HERE/files" ]; then
  FILES_PAYLOAD=$HERE/files
elif [ -d "$HERE/../files" ]; then
  FILES_PAYLOAD=$HERE/../files
else
  die "the files/ payload is missing next to the installer or its parent directory"
fi
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
PAYLOAD_ARCHIVE=""
cleanup() { # a failed run must not leave the target mounted (the next run's guard would refuse)
  local rc=$?
  [ "$DRY" = 1 ] && return 0
  [ -z "$PAYLOAD_ARCHIVE" ] || rm -f -- "$PAYLOAD_ARCHIVE"
  if [ "$rc" != 0 ] && [ -s "$T/debootstrap/debootstrap.log" ]; then
    echo "install-btrfs: failed (rc $rc); tail of debootstrap.log:" >&2
    tail -n 60 "$T/debootstrap/debootstrap.log" >&2 || true
  fi
  umount -R "$T" 2>/dev/null || true
  umount "$TOP" 2>/dev/null || true
}
trap cleanup EXIT
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
# The initial state ID records when its Btrfs subvolume is created, not when disk partitioning starts.
STATE_TIMESTAMP=$(date -u +%Y%m%d%H%M%S)
STATE=$(printf '@os-000001-%s-%sZ-install' \
  "${STATE_TIMESTAMP:0:8}" "${STATE_TIMESTAMP:8:6}")
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
run mount -o umask=0077 "$ESP" "$T/boot/efi"

# Disable systemd's stock BLS writer before debootstrap configures systemd-boot and the kernel. Its package
# postinst can call kernel-install during debootstrap, before the dr-state hooks are installed below.
if [ "$BOOTLOADER" = systemd-boot ]; then
  run mkdir -p "$T/etc/kernel/install.d"
  run ln -s /dev/null "$T/etc/kernel/install.d/90-loaderentry.install"
fi

BOOT_PACKAGES=grub-efi-amd64
[ "$BOOTLOADER" = systemd-boot ] && BOOT_PACKAGES=systemd-boot,systemd-boot-efi
run debootstrap --include=linux-image-amd64,"$BOOT_PACKAGES",btrfs-progs,initramfs-tools,systemd-sysv,python3,openssh-server,sudo,locales,ifupdown,isc-dhcp-client,dosfstools,ca-certificates "$SUITE" "$T" "$MIRROR"

if [ "$BOOTLOADER" = systemd-boot ] && [ "$DRY" != 1 ]; then
  bls_override="$T/etc/kernel/install.d/90-loaderentry.install"
  [ -L "$bls_override" ] && [ "$(readlink "$bls_override")" = /dev/null ] \
    || die "the systemd-boot BLS writer override did not survive debootstrap"
fi

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

# BEGIN payload-copy helpers
payload_members() ( # payload_members SOURCE TARGET: NUL-delimited archive members
  local source=$1 target=$2 child relative
  cd "$source" || return
  shopt -s dotglob nullglob
  for child in *; do
    if [ ! -d "$child" ] || [ -L "$child" ]; then
      printf '%s\0' "$child"
      continue
    fi
    # Do not archive existing destination directories: tar may change their owner or mode. Descendants still get
    # copied, so /usr/local itself and Debian's pre-existing 2775 root:staff directory stay untouched.
    find "$child" -mindepth 1 -print0 |
      while IFS= read -r -d '' relative; do
        case "$relative" in
          __pycache__|__pycache__/*|*/__pycache__|*/__pycache__/*|*.pyc|*.pyo)
            continue
            ;;
        esac
        if [ -d "$relative" ] && [ ! -L "$relative" ] && [ -d "$target/$relative" ]; then
          continue
        fi
        printf '%s\0' "$relative"
      done || return
  done
)

files_payload_archive() { # files_payload_archive SOURCE TARGET ARCHIVE
  local source=$1 target=$2 archive=$3
  payload_members "$source" "$target" |
    tar -C "$source" --null --owner=0 --group=0 --numeric-owner --no-recursion --files-from=- -cf "$archive"
}

payload_archive_has_bytecode() { # payload_archive_has_bytecode ARCHIVE
  local archive=$1 member members
  members=$(tar -tf "$archive") || return 2
  while IFS= read -r member; do
    case "/$member/" in
      */__pycache__/*) return 0 ;;
    esac
    case "$member" in
      *.pyc|*.pyo) return 0 ;;
    esac
  done <<<"$members"
  return 1
}

extract_files_payload() { # extract_files_payload TARGET ARCHIVE
  local target=$1 archive=$2 status
  local -a argv=(tar -C "$target" --same-owner -xf "$archive")
  if [ "$DRY" = 1 ]; then
    printf '+' >&2
    printf ' %q' "${argv[@]}" >&2
    printf '\n' >&2
    return
  fi

  if payload_archive_has_bytecode "$archive"; then
    echo "generated bytecode is present in the payload archive" >&2
    return 1
  else
    status=$?
    [ "$status" = 1 ] || return "$status"
  fi
  "${argv[@]}"
}

install_files_payload() { # install_files_payload SOURCE TARGET
  local source=$1 target=$2 member archive
  if [ "$DRY" = 1 ]; then
    archive=${PAYLOAD_ARCHIVE:-PAYLOAD_ARCHIVE}
    echo "+ payload_members $source $target | tar -C $source --null --owner=0 --group=0 --numeric-owner --no-recursion --files-from=- -cf $archive" >&2
    payload_members "$source" "$target" |
      while IFS= read -r -d '' member; do
        printf '+ payload %s/%s\n' "$target" "$member" >&2
      done || return
    extract_files_payload "$target" "$archive"
    return
  fi

  PAYLOAD_ARCHIVE=$(mktemp) || return
  files_payload_archive "$source" "$target" "$PAYLOAD_ARCHIVE" || return
  extract_files_payload "$target" "$PAYLOAD_ARCHIVE" || return
  rm -f -- "$PAYLOAD_ARCHIVE" || return
  PAYLOAD_ARCHIVE=""
}
# END payload-copy helpers

install_files_payload "$FILES_PAYLOAD/usr" "$T/usr"
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
  chroot_run /usr/local/sbin/dr-state install-systemd-boot
  # Debian's systemd-boot kernel hooks call kernel-install, which creates an extra machine-id BLS entry and kernel
  # copy outside dr-state's per-state ESP layout. The earlier BLS writer override suppresses that output; divert the
  # package hooks too, and let zz-dr-state own kernel updates.
  # Keep diverted files outside the run-parts directories: --lsbsysinit accepts the usual .distrib suffix.
  chroot_run mkdir -p /etc/kernel/dr-disabled
  for d in postinst.d postrm.d; do
    hook="/etc/kernel/$d/zz-systemd-boot"
    target="/etc/kernel/dr-disabled/${d%.d}-zz-systemd-boot"
    chroot_run dpkg-divert --local --add --rename --divert "$target" "$hook"
  done
fi
chroot_run /usr/local/sbin/dr-state initialize-state "$STATE" "$STATE_TIMESTAMP"
chroot_run /usr/local/sbin/dr-state gen-menu --default "$STATE"

# The BLS writer override is installed before debootstrap. Still refuse to finish if any package hook bypassed it and
# left an unowned entry that could point at the top-level subvolume.
if [ "$BOOTLOADER" = systemd-boot ] && [ "$DRY" != 1 ]; then
  BLS_ENTRY_DIR="$T/boot/efi/loader/entries"
  if [ -d "$BLS_ENTRY_DIR" ]; then
    while IFS= read -r -d '' bls_entry; do
      [ -f "$bls_entry" ] && [ ! -L "$bls_entry" ] \
        || die "unexpected non-regular systemd-boot entry $bls_entry; refusing to finish the installation"
      [ "$(head -n 1 "$bls_entry" | tr -d '\r')" = '# generated by dr-state sdboot; do not edit' ] \
        || die "unowned systemd-boot entry $bls_entry remains after debootstrap; refusing to finish the installation"
    done < <(find "$BLS_ENTRY_DIR" -mindepth 1 -maxdepth 1 -name '*.conf' -print0)
  fi
fi

# Hooks activate only after the initial state and default menu exist. apt snapshots before every dpkg run and
# refreshes the menu afterwards; kernel hooks refresh it after installs and removals. DR_STATE=none skips the snapshot.
install_files_payload "$FILES_PAYLOAD/etc" "$T/etc"

run rm -f "$T/run/dr-state.lock"   # the lock file must not be carried into the state (and its snapshots)
run umount -R "$T"
run umount "$TOP"
trap - EXIT
echo "install-btrfs: done. Shut the guest down and boot from $DEVICE alone."
