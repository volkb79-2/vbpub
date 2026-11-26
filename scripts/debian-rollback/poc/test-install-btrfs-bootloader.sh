#!/usr/bin/env bash
# Offline dry-run test of install-btrfs.sh's bootloader selection. No disk is touched and no command from the printed
# install plan is executed.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/install-btrfs.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
cases=0

bad() { echo "FAIL $*"; fail=1; }

run_dry() { # run_dry LABEL ARGS...
  local label=$1; shift
  cases=$((cases + 1))
  "$INSTALL" --dry-run --yes --device /dev/dr-not-used "$@" >"$WORK/$label.out" 2>"$WORK/$label.err"
  local rc=$?
  [ "$rc" = 0 ] || bad "$label: expected exit 0, got $rc"
}

contains() { # contains LABEL FILE NEEDLE
  cases=$((cases + 1))
  grep -Fq -- "$3" "$2" || bad "$1: '$2' lacks '$3'"
}

absent() {
  cases=$((cases + 1))
  if grep -Fq -- "$3" "$2"; then bad "$1: '$2' unexpectedly contains '$3'"; fi
}

assert_include() { # assert_include LABEL FILE PACKAGE_LIST
  local label=$1 file=$2 want=$3 got
  cases=$((cases + 1))
  got=$(grep -o -- '--include=[^ ]*' "$file" | head -n1)
  [ "$got" = "--include=$want" ] || bad "$label debootstrap include list is '$got', want '$want'"
}

assert_payload_order() { # assert_payload_order LABEL DRY_TRACE
  local label=$1 file=$2 usrp ui gm ea epi epr before_menu
  usrp=$(grep -nF '+ payload /mnt/drtarget/usr/local/sbin/dr-state' "$file" | cut -d: -f1)
  ui=$(grep -nF 'update-initramfs -u -k all' "$file" | cut -d: -f1)
  gm=$(grep -nF 'gen-menu --default @os-000001-' "$file" | cut -d: -f1)
  ea=$(grep -nF '+ payload /mnt/drtarget/etc/apt/apt.conf.d/80dr-state' "$file" | cut -d: -f1)
  epi=$(grep -nF '+ payload /mnt/drtarget/etc/kernel/postinst.d/zz-dr-state' "$file" | cut -d: -f1)
  epr=$(grep -nF '+ payload /mnt/drtarget/etc/kernel/postrm.d/zz-dr-state' "$file" | cut -d: -f1)
  cases=$((cases + 1))
  if [ -z "$usrp" ] || [ -z "$ui" ] || [ -z "$gm" ] || [ -z "$ea" ] || [ -z "$epi" ] || [ -z "$epr" ]; then
    bad "$label payload activation markers are incomplete"
    return
  fi
  before_menu=$(sed -n "1,$((gm - 1))p" "$file" 2>/dev/null || true)
  cases=$((cases + 1))
  if grep -Fq '/mnt/drtarget/etc/apt/apt.conf.d/80dr-state' <<<"$before_menu" \
      || grep -Fq '/mnt/drtarget/etc/kernel/postinst.d/zz-dr-state' <<<"$before_menu" \
      || grep -Fq '/mnt/drtarget/etc/kernel/postrm.d/zz-dr-state' <<<"$before_menu"; then
    bad "$label activated a hook payload member before gen-menu --default"
  fi
  cases=$((cases + 1))
  if [ "$usrp" -lt "$ui" ] && [ "$gm" -lt "$ea" ] && [ "$gm" -lt "$epi" ] && [ "$gm" -lt "$epr" ]; then
    :
  else
    bad "$label usr payload or post-gen-menu hook activation order is wrong"
  fi
}

run_dry grub-default
contains grub-default-package "$WORK/grub-default.err" \
  '--include=linux-image-amd64,grub-efi-amd64,btrfs-progs'
contains python3-package "$WORK/grub-default.err" 'systemd-sysv,python3,openssh-server'
assert_include grub "$WORK/grub-default.err" \
  linux-image-amd64,grub-efi-amd64,btrfs-progs,initramfs-tools,systemd-sysv,python3,openssh-server,sudo,locales,ifupdown,isc-dhcp-client,dosfstools,ca-certificates
contains grub-default-install "$WORK/grub-default.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state install-grub'
absent grub-default-no-state-helper "$WORK/grub-default.err" '_state-name'
absent grub-default-no-systemd-package "$WORK/grub-default.err" 'systemd-boot,'
absent grub-default-no-snapper "$WORK/grub-default.err" ',snapper'

run_dry grub-explicit --bootloader grub
contains grub-explicit-package "$WORK/grub-explicit.err" \
  '--include=linux-image-amd64,grub-efi-amd64,btrfs-progs'

run_dry sdboot --bootloader systemd-boot
assert_include systemd-boot "$WORK/sdboot.err" \
  linux-image-amd64,systemd-boot,systemd-boot-efi,btrfs-progs,initramfs-tools,systemd-sysv,python3,openssh-server,sudo,locales,ifupdown,isc-dhcp-client,dosfstools,ca-certificates
contains sdboot-packages "$WORK/sdboot.err" \
  '--include=linux-image-amd64,systemd-boot,systemd-boot-efi,btrfs-progs'
contains sdboot-bootloader-install "$WORK/sdboot.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state install-systemd-boot'
contains sdboot-divert-dir "$WORK/sdboot.err" \
  'chroot /mnt/drtarget mkdir -p /etc/kernel/dr-disabled'
contains sdboot-divert-postinst "$WORK/sdboot.err" \
  'chroot /mnt/drtarget dpkg-divert --local --add --rename --divert /etc/kernel/dr-disabled/postinst-zz-systemd-boot /etc/kernel/postinst.d/zz-systemd-boot'
contains sdboot-divert-postrm "$WORK/sdboot.err" \
  'chroot /mnt/drtarget dpkg-divert --local --add --rename --divert /etc/kernel/dr-disabled/postrm-zz-systemd-boot /etc/kernel/postrm.d/zz-systemd-boot'
contains sdboot-disable-kernel-install-bls-dir "$WORK/sdboot.err" \
  '+ mkdir -p /mnt/drtarget/etc/kernel/install.d'
contains sdboot-disable-kernel-install-bls "$WORK/sdboot.err" \
  '+ ln -s /dev/null /mnt/drtarget/etc/kernel/install.d/90-loaderentry.install'
contains restricted-esp-install-mount "$WORK/sdboot.err" 'mount -o umask=0077'
absent sdboot-no-grub-package "$WORK/sdboot.err" 'linux-image-amd64,grub-efi-amd64'
absent sdboot-no-grub-installer "$WORK/sdboot.err" 'dr-state install-grub'
absent sdboot-no-snapper "$WORK/sdboot.err" ',snapper'
contains sdboot-menu-lesson "$WORK/sdboot.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state gen-menu --default @os-'
contains initial-state-captured "$WORK/sdboot.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state initialize-state @os-000001-'
contains normalized-payload-owners "$WORK/sdboot.err" \
  '--owner=0 --group=0'
contains normalized-payload-extract "$WORK/sdboot.err" \
  'tar -C /mnt/drtarget/usr --same-owner -xf PAYLOAD_ARCHIVE'
absent payload-copy-preserves-builder "$WORK/sdboot.err" 'cp -a '
absent grub-no-systemd-hook-diversion "$WORK/grub-default.err" 'dpkg-divert --local --add --rename'
contains dry-usr-payload-archive "$WORK/sdboot.err" \
  "+ payload_members $HERE/../files/usr /mnt/drtarget/usr | tar -C $HERE/../files/usr --null --owner=0 --group=0 --numeric-owner --no-recursion --files-from=- -cf PAYLOAD_ARCHIVE"
contains dry-hook-payload-archive "$WORK/sdboot.err" \
  "+ payload_members $HERE/../files/etc /mnt/drtarget/etc | tar -C $HERE/../files/etc --null --owner=0 --group=0 --numeric-owner --no-recursion --files-from=- -cf PAYLOAD_ARCHIVE"

HOOK_ROOT=$HERE/../files/etc
cat >"$WORK/80dr-state.want" <<'EOF'
DPkg::Tools::Options::/usr/local/sbin/dr-state::Version "2";
DPkg::Tools::Options::/usr/local/sbin/dr-state::InfoFD "3";
DPkg::Pre-Install-Pkgs { "/usr/local/sbin/dr-state apt-pre-install-pkgs"; };
DPkg::Pre-Invoke { "[ ! -x /usr/local/sbin/dr-state ] || /usr/local/sbin/dr-state snapshot --reason apt || exit 100"; };
DPkg::Post-Invoke { "[ ! -x /usr/local/sbin/dr-state ] || /usr/local/sbin/dr-state gen-menu || true"; };
EOF
cat >"$WORK/zz-dr-state.want" <<'EOF'
#!/bin/sh
# regenerate the ESP boot menu after a kernel is installed or removed
[ -x /usr/local/sbin/dr-state ] || exit 0
/usr/local/sbin/dr-state gen-menu || echo "dr-state: gen-menu failed, boot menu not refreshed" >&2
exit 0
EOF
for spec in \
  "apt/apt.conf.d/80dr-state:644:$WORK/80dr-state.want" \
  "kernel/postinst.d/zz-dr-state:755:$WORK/zz-dr-state.want" \
  "kernel/postrm.d/zz-dr-state:755:$WORK/zz-dr-state.want"; do
  path=${spec%%:*}; rest=${spec#*:}; mode=${rest%%:*}; want=${rest#*:}
  cases=$((cases + 1))
  cmp -s "$want" "$HOOK_ROOT/$path" || bad "hook file $path differs byte-for-byte from the retired heredoc"
  cases=$((cases + 1))
  got=$(stat -c %a "$HOOK_ROOT/$path")
  [ "$got" = "$mode" ] || bad "hook file $path mode is $got, want $mode"
done

# Assemble the archive with the production helper as the lane's non-root user. The archive records root ownership;
# extraction as root then reproduces it. Existing destination directories are omitted so extraction cannot alter them.
cases=$((cases + 1))
[ "$(id -u)" != 0 ] || bad "payload ownership test requires the lane's non-root uid"
sed -n '/^# BEGIN payload-copy helpers$/,/^# END payload-copy helpers$/p' \
  "$INSTALL" >"$WORK/payload-helpers.sh"
# shellcheck disable=SC1091  # extracted from the installer under test
source "$WORK/payload-helpers.sh"

PAYLOAD_SOURCE=$WORK/payload-source
PAYLOAD_TARGET=$WORK/payload-target
mkdir -p \
  "$PAYLOAD_SOURCE/usr/local/sbin" \
  "$PAYLOAD_SOURCE/usr/local/lib/dr-state/dr_state" \
  "$PAYLOAD_SOURCE/etc/apt/apt.conf.d" \
  "$PAYLOAD_SOURCE/etc/kernel/postinst.d" \
  "$PAYLOAD_SOURCE/etc/kernel/postrm.d" \
  "$PAYLOAD_TARGET/usr/local/lib/dr-state/dr_state" \
  "$PAYLOAD_TARGET/usr/local/lib" \
  "$PAYLOAD_TARGET/usr/local/sbin" \
  "$PAYLOAD_TARGET/etc/apt/apt.conf.d" \
  "$PAYLOAD_TARGET/etc/kernel/postinst.d" \
  "$PAYLOAD_TARGET/etc/kernel/postrm.d"
printf 'launcher\n' >"$PAYLOAD_SOURCE/usr/local/sbin/dr-state"
printf 'core\n' >"$PAYLOAD_SOURCE/usr/local/lib/dr-state/dr_state/core.py"
cp "$WORK/80dr-state.want" "$PAYLOAD_SOURCE/etc/apt/apt.conf.d/80dr-state"
cp "$WORK/zz-dr-state.want" "$PAYLOAD_SOURCE/etc/kernel/postinst.d/zz-dr-state"
cp "$WORK/zz-dr-state.want" "$PAYLOAD_SOURCE/etc/kernel/postrm.d/zz-dr-state"
chmod 0755 "$PAYLOAD_SOURCE/usr/local/sbin/dr-state"
chmod 0644 "$PAYLOAD_SOURCE/usr/local/lib/dr-state/dr_state/core.py"
chmod 0644 "$PAYLOAD_SOURCE/etc/apt/apt.conf.d/80dr-state"
chmod 0755 "$PAYLOAD_SOURCE/etc/kernel/postinst.d/zz-dr-state" \
  "$PAYLOAD_SOURCE/etc/kernel/postrm.d/zz-dr-state"
mkdir -p "$PAYLOAD_TARGET/usr/local/__pycache__"
printf 'stale\n' >"$PAYLOAD_TARGET/usr/local/__pycache__/keep.pyc"
chmod 0711 "$PAYLOAD_TARGET/usr"
chmod 2775 "$PAYLOAD_TARGET/usr/local"
chmod 0700 "$PAYLOAD_TARGET/usr/local/sbin"
chmod 0750 "$PAYLOAD_TARGET/etc"

PAYLOAD_USR_ARCHIVE=$WORK/payload-usr.tar
PAYLOAD_ETC_ARCHIVE=$WORK/payload-etc.tar
payload_members "$PAYLOAD_SOURCE/usr" "$PAYLOAD_TARGET/usr" |
  tar -C "$PAYLOAD_SOURCE/usr" --null --owner=0 --group=0 --numeric-owner \
    --no-recursion --files-from=- -cf "$PAYLOAD_USR_ARCHIVE"
payload_members "$PAYLOAD_SOURCE/etc" "$PAYLOAD_TARGET/etc" |
  tar -C "$PAYLOAD_SOURCE/etc" --null --owner=0 --group=0 --numeric-owner \
    --no-recursion --files-from=- -cf "$PAYLOAD_ETC_ARCHIVE"
tar --numeric-owner -tvf "$PAYLOAD_USR_ARCHIVE" >"$WORK/payload.attrs"
tar --numeric-owner -tvf "$PAYLOAD_ETC_ARCHIVE" >>"$WORK/payload.attrs"
tar -tf "$PAYLOAD_USR_ARCHIVE" >"$WORK/payload.names"
tar -tf "$PAYLOAD_ETC_ARCHIVE" >>"$WORK/payload.names"
for member in \
  local/sbin/dr-state \
  local/lib/dr-state/dr_state/core.py \
  apt/apt.conf.d/80dr-state \
  kernel/postinst.d/zz-dr-state \
  kernel/postrm.d/zz-dr-state; do
  cases=$((cases + 1))
  line=$(grep -F " $member" "$WORK/payload.attrs" || true)
  case "$line" in
    *" 0/0 "*) ;;
    *) bad "payload archive member $member is not uid/gid 0: '$line'" ;;
  esac
done
for existing in \
  local local/sbin local/lib local/lib/dr-state local/lib/dr-state/dr_state \
  apt apt/apt.conf.d kernel kernel/postinst.d kernel/postrm.d; do
  cases=$((cases + 1))
  grep -Fxq "$existing/" "$WORK/payload.names" \
    && bad "payload archive would rewrite existing target directory $existing"
done
for pair in \
  "local/sbin/dr-state:-rwxr-xr-x" \
  "local/lib/dr-state/dr_state/core.py:-rw-r--r--" \
  "apt/apt.conf.d/80dr-state:-rw-r--r--" \
  "kernel/postinst.d/zz-dr-state:-rwxr-xr-x" \
  "kernel/postrm.d/zz-dr-state:-rwxr-xr-x"; do
  member=${pair%:*}; want=${pair##*:}
  cases=$((cases + 1))
  got=$(awk -v member="$member" '$NF == member { print $1 }' "$WORK/payload.attrs")
  [ "$got" = "$want" ] || bad "payload archive member $member mode is '$got', want '$want'"
done

# Root ownership was verified above. For this non-root extraction fixture, use the lane uid/gid so --same-owner
# exercises the production extractor without relying on the lane's ability to chown files to root.
# shellcheck disable=SC2329 # sourced install_files_payload resolves this test override at runtime
files_payload_archive() { # files_payload_archive SOURCE TARGET ARCHIVE (test uid/gid)
  local source=$1 target=$2 archive=$3 uid gid
  uid=$(id -u)
  gid=$(id -g)
  payload_members "$source" "$target" |
    tar -C "$source" --null --owner="$uid" --group="$gid" --numeric-owner --no-recursion --files-from=- -cf "$archive"
}

# shellcheck disable=SC2034 # sourced extract_files_payload reads this for the dry-run trace
DRY=1
# shellcheck disable=SC2034 # sourced install_files_payload passes this placeholder to extract_files_payload
PAYLOAD_ARCHIVE=""
install_files_payload "$PAYLOAD_SOURCE/usr" "$PAYLOAD_TARGET/usr" 2>"$WORK/usr-extract-dry.err"
contains shared-extract-argv "$WORK/usr-extract-dry.err" \
  "+ tar -C $PAYLOAD_TARGET/usr --same-owner -xf PAYLOAD_ARCHIVE"
DRY=0
install_files_payload "$PAYLOAD_SOURCE/usr" "$PAYLOAD_TARGET/usr"
install_files_payload "$PAYLOAD_SOURCE/etc" "$PAYLOAD_TARGET/etc"
for pair in "usr:711" "usr/local:2775" "usr/local/sbin:700" "etc:750"; do
  path=${pair%:*}; want=${pair##*:}
  cases=$((cases + 1))
  got=$(stat -c %a "$PAYLOAD_TARGET/$path")
  [ "$got" = "$want" ] || bad "payload copy changed existing directory $path mode $want -> $got"
done
cases=$((cases + 1))
[ -f "$PAYLOAD_TARGET/usr/local/__pycache__/keep.pyc" ] \
  || bad "payload copy deleted a foreign destination file"
cases=$((cases + 1))
[ -z "$(find "$PAYLOAD_TARGET" -type d -name __pycache__ ! -path "$PAYLOAD_TARGET/usr/local/__pycache__" -print -quit)" ] \
  || bad "payload copy introduced generated bytecode"

mkdir -p "$WORK/bytecode-source/usr/local/__pycache__"
printf 'generated bytecode\n' >"$WORK/bytecode-source/usr/local/__pycache__/bad.pyc"
tar -C "$WORK/bytecode-source" -cf "$WORK/bytecode.tar" usr/local/__pycache__/bad.pyc
cases=$((cases + 1))
if extract_files_payload "$PAYLOAD_TARGET" "$WORK/bytecode.tar" 2>"$WORK/bytecode.err"; then
  bad "payload extractor accepted bytecode in the archive"
fi
contains bytecode-archive-guard "$WORK/bytecode.err" \
  'generated bytecode is present in the payload archive'

mkdir -p "$WORK/find-failure-source/a-fail" "$WORK/find-failure-source/z-good" "$WORK/fake-find"
REAL_FIND=$(command -v find)
cat >"$WORK/fake-find/find" <<EOF
#!/bin/sh
if [ "\$1" = a-fail ]; then exit 19; fi
exec "$REAL_FIND" "\$@"
EOF
chmod +x "$WORK/fake-find/find"
cases=$((cases + 1))
if ( PATH="$WORK/fake-find:$PATH"; hash -r; \
     payload_members "$WORK/find-failure-source" "$WORK/find-failure-target" >/dev/null ); then
  bad "payload_members masked a find failure inside its child loop"
fi

for pair in \
  "etc/apt/apt.conf.d/80dr-state:644" \
  "etc/kernel/postinst.d/zz-dr-state:755" \
  "etc/kernel/postrm.d/zz-dr-state:755"; do
  path=${pair%:*}; want=${pair##*:}
  cases=$((cases + 1))
  got=$(stat -c %a "$PAYLOAD_TARGET/$path")
  [ "$got" = "$want" ] || bad "extracted hook $path mode is $got, want $want"
done

# The BLS override must precede debootstrap's systemd-boot/kernel package configuration; hook diversions and
# dr-state installation follow package installation but precede state capture and menu generation.
cases=$((cases + 1))
kd=$(grep -nF 'mkdir -p /mnt/drtarget/etc/kernel/install.d' "$WORK/sdboot.err" | cut -d: -f1)
kp=$(grep -nF 'ln -s /dev/null /mnt/drtarget/etc/kernel/install.d/90-loaderentry.install' "$WORK/sdboot.err" | cut -d: -f1)
dp=$(grep -nF 'debootstrap --include=linux-image-amd64,systemd-boot,systemd-boot-efi' "$WORK/sdboot.err" | cut -d: -f1)
bc=$(grep -nF 'install-systemd-boot' "$WORK/sdboot.err" | cut -d: -f1)
si=$(grep -nF 'initialize-state @os-000001-' "$WORK/sdboot.err" | cut -d: -f1)
dd=$(grep -nF 'mkdir -p /etc/kernel/dr-disabled' "$WORK/sdboot.err" | cut -d: -f1)
pi=$(grep -nF 'dpkg-divert --local --add --rename --divert /etc/kernel/dr-disabled/postinst-zz-systemd-boot' "$WORK/sdboot.err" | cut -d: -f1)
pr=$(grep -nF 'dpkg-divert --local --add --rename --divert /etc/kernel/dr-disabled/postrm-zz-systemd-boot' "$WORK/sdboot.err" | cut -d: -f1)
gm=$(grep -nF 'gen-menu --default @os-000001-' "$WORK/sdboot.err" | cut -d: -f1)
[ -n "$kd" ] && [ -n "$kp" ] && [ -n "$dp" ] && [ -n "$bc" ] && [ -n "$dd" ] && [ -n "$pi" ] && [ -n "$pr" ] && [ -n "$si" ] && [ -n "$gm" ] \
  && [ "$kd" -lt "$kp" ] && [ "$kp" -lt "$dp" ] && [ "$dp" -lt "$bc" ] \
  && [ "$bc" -lt "$dd" ] && [ "$dd" -lt "$pi" ] && [ "$pi" -lt "$pr" ] \
  && [ "$pr" -lt "$si" ] && [ "$si" -lt "$gm" ] \
  || bad "sdboot BLS override must precede debootstrap, and bootloader setup/state capture must precede gen-menu"
assert_payload_order sdboot "$WORK/sdboot.err"
assert_payload_order grub "$WORK/grub-default.err"

cases=$((cases + 1))
if "$INSTALL" --dry-run --yes --device /dev/dr-not-used --bootloader grub2 >/dev/null 2>&1; then
  bad "invalid bootloader accepted"
fi

[ "$fail" = 0 ] && echo "test-install-btrfs-bootloader: OK ($cases cases)"
exit "$fail"
