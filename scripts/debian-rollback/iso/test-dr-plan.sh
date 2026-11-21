#!/usr/bin/env bash
# Offline test of the wipe guard (iso/dr-plan): no disk, no guest, no root. dr-plan is a pure function of
#   - `lsblk -dnbP -o NAME,TYPE,ROTA,RM,SIZE,TRAN` output (a file),
#   - the install config (a file, or none),
#   - the names the caller excludes (the booted ISO's disk, the config stick, anything mounted),
# and prints DECISION=install|confirm|refuse plus TARGET=/dev/NAME. exit 0 = install or confirm, exit 3 = refuse.
#
# Written BEFORE dr-plan existed (failing-first, controller condition 2 of 2026-11-18): the first run of this file
# must fail for every case because there is no dr-plan yet.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAN="$HERE/dr-plan"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0

GIB=$((1024 * 1024 * 1024))
disk() { printf 'NAME="%s" TYPE="disk" ROTA="%s" RM="%s" SIZE="%s" TRAN="%s"\n' "$1" "$2" "$3" "$(($4 * GIB))" "${5:-}"; }

ONE=$WORK/one.lsblk;   disk vda 0 0 20 > "$ONE"
TWO=$WORK/two.lsblk;   { disk vda 1 0 20; disk vdb 0 0 20; } > "$TWO"
THREE=$WORK/three.lsblk; { disk sda 1 0 500; disk nvme0n1 0 0 256 nvme; disk sdb 0 0 500 sata; } > "$THREE"
USB=$WORK/usb.lsblk;   { disk vda 0 0 20; disk sdb 0 1 16 usb; } > "$USB"
SMALL=$WORK/small.lsblk; { disk vda 0 0 4; } > "$SMALL"
NONE=$WORK/none.lsblk; : > "$NONE"
{ disk vda 0 0 20; printf 'NAME="loop0" TYPE="loop" ROTA="0" RM="0" SIZE="%s" TRAN=""\nNAME="sr0" TYPE="rom" ROTA="1" RM="1" SIZE="%s" TRAN="sata"\n' "$((GIB))" "$((2 * GIB))"; } > "$WORK/loop.lsblk"
printf 'NAME="vda1" TYPE="part" ROTA="0" RM="0" SIZE="%s" TRAN=""\nNAME="vda" TYPE="disk" ROTA="0" RM="0" SIZE="%s" TRAN=""\n' "$GIB" "$((20 * GIB))" > "$WORK/part.lsblk"

conf() { printf '%s\n' "$@" > "$WORK/conf"; echo "$WORK/conf"; }

# expect LABEL WANT_DECISION WANT_TARGET LSBLK CONFIG|none [extra dr-plan args...]
expect() {
  local label=$1 want=$2 target=$3 lsblk=$4 cfg=$5; shift 5
  local args=(--lsblk "$lsblk"); if [ "$cfg" = none ]; then args+=(--no-config); else args+=(--config "$cfg"); fi
  local out rc
  out=$("$PLAN" "${args[@]}" "$@" 2>"$WORK/err"); rc=$?
  local dec; dec=$(sed -n 's/^DECISION=//p' <<<"$out" | head -n1)
  local tgt; tgt=$(sed -n 's/^TARGET=//p' <<<"$out" | head -n1)
  local want_rc=0; [ "$want" = refuse ] && want_rc=3
  if [ "$dec" != "$want" ] || [ "$rc" != "$want_rc" ] || [ "$tgt" != "$target" ]; then
    echo "FAIL $label: want decision=$want target='$target' rc=$want_rc; got decision='$dec' target='$tgt' rc=$rc ($(head -c 200 "$WORK/err" | tr '\n' ' '))"
    fail=1
  fi
}

# --- the safe default: no explicit target
expect single-disk-no-target-installs        install /dev/vda "$ONE"  "$(conf 'HOSTNAME=h1')"
expect two-disks-no-target-REFUSES           refuse  ""       "$TWO"  "$(conf 'HOSTNAME=h1')"
expect three-disks-no-target-REFUSES         refuse  ""       "$THREE" "$(conf 'HOSTNAME=h1')"
expect no-disk-REFUSES                       refuse  ""       "$NONE" "$(conf 'TARGET_DISK=first-ssd')"
# --- the operator's rule, but only when asked for explicitly
expect first-ssd-prefers-nvme                install /dev/nvme0n1 "$THREE" "$(conf 'TARGET_DISK=first-ssd')"
expect first-ssd-over-rotational             install /dev/vdb "$TWO" "$(conf 'TARGET_DISK=first-ssd')"
expect first-ssd-excludes-iso-disk           install /dev/vda "$TWO" "$(conf 'TARGET_DISK=first-ssd')" --exclude vdb
# --- explicit target
expect explicit-target-on-two-disks          install /dev/vda "$TWO" "$(conf 'TARGET_DISK=/dev/vda')"
expect explicit-target-by-kernel-name        install /dev/vdb "$TWO" "$(conf 'TARGET_DISK=vdb')"
expect explicit-target-is-iso-disk-REFUSES   refuse  ""       "$TWO" "$(conf 'TARGET_DISK=/dev/vdb')" --exclude vdb
expect explicit-target-removable-REFUSES     refuse  ""       "$USB" "$(conf 'TARGET_DISK=/dev/sdb')"
expect explicit-target-too-small-REFUSES     refuse  ""       "$SMALL" "$(conf 'TARGET_DISK=/dev/vda')"
expect explicit-target-missing-REFUSES       refuse  ""       "$ONE" "$(conf 'TARGET_DISK=/dev/vdz')"
expect explicit-target-partition-REFUSES     refuse  ""       "$WORK/part.lsblk" "$(conf 'TARGET_DISK=/dev/vda1')"
expect explicit-target-injection-REFUSES     refuse  ""       "$TWO" "$(conf 'TARGET_DISK=/dev/vda;reboot')"
expect explicit-target-dotdot-REFUSES        refuse  ""       "$TWO" "$(conf 'TARGET_DISK=/dev/../etc/passwd')"
expect explicit-target-empty-value-two-disks  refuse  ""       "$TWO" "$(conf 'TARGET_DISK=')"      # empty = unset, and two disks need a target
expect explicit-target-empty-value-is-unset  install /dev/vda "$ONE" "$(conf 'TARGET_DISK=')"      # empty = unset: the one-disk rule applies (not an error)
# --- the config stick / mounted disks are excluded by the caller, which can leave exactly one disk
expect config-stick-excluded-leaves-one      install /dev/vda "$USB" "$(conf 'HOSTNAME=h1')" --exclude sdb
# review 2026-11-18: "the only disk" means the only internal disk of any kind, not the only one that qualifies
expect usb-stick-NOT-excluded-REFUSES        refuse  ""       "$USB" "$(conf 'HOSTNAME=h1')"
SMALLBIG=$WORK/smallbig.lsblk; { disk vda 0 0 4; disk vdb 0 0 500; } > "$SMALLBIG"
expect small-boot-disk-plus-big-REFUSES      refuse  ""       "$SMALLBIG" "$(conf 'HOSTNAME=h1')"
expect small-boot-disk-explicit-big-ok       install /dev/vdb "$SMALLBIG" "$(conf 'TARGET_DISK=/dev/vdb')"
expect usb-stick-NOT-excluded-no-config      refuse  ""       "$USB" none
# review round 11: a disk with something mounted ("busy") is never a target but still COUNTS as an internal disk
expect busy-second-disk-REFUSES              refuse  ""       "$TWO" "$(conf 'HOSTNAME=h1')" --busy vdb
expect busy-second-disk-no-config-REFUSES    refuse  ""       "$TWO" none --busy vdb
expect busy-explicit-target-REFUSES          refuse  ""       "$TWO" "$(conf 'TARGET_DISK=/dev/vdb')" --busy vdb
expect busy-explicit-other-target-ok         install /dev/vda "$TWO" "$(conf 'TARGET_DISK=/dev/vda')" --busy vdb
expect first-ssd-skips-busy-disk             install /dev/sdb "$THREE" "$(conf 'TARGET_DISK=first-ssd')" --busy nvme0n1
expect busy-and-excluded-are-different       install /dev/vda "$TWO" "$(conf 'HOSTNAME=h1')" --exclude vdb
expect busy-names-validated                  refuse  ""       "$TWO" "$(conf 'TARGET_DISK=/dev/vda')" --busy 'vdb;x'
expect rom-and-loop-do-not-count             install /dev/vda "$WORK/loop.lsblk" "$(conf 'HOSTNAME=h1')"
# root password hash (never a plaintext password in the ISO or in a process list)
# shellcheck disable=SC2016  # crypt hashes contain literal dollar signs
expect password-hash-ok                      install /dev/vda "$ONE" "$(conf 'ROOT_PASSWORD_HASH=$y$j9T$abcdefgh$XYZ.123/abc')"
# shellcheck disable=SC2016
expect password-hash-bad-REFUSES             refuse  ""       "$ONE" "$(conf 'ROOT_PASSWORD_HASH=plaintext')"
# review r13: components must be non-empty and there must be id, salt and hash
# shellcheck disable=SC2016
expect password-hash-empty-salt-REFUSES      refuse  ""       "$ONE" "$(conf 'ROOT_PASSWORD_HASH=$6$$abcdef')"
# shellcheck disable=SC2016
expect password-hash-no-hash-REFUSES         refuse  ""       "$ONE" "$(conf 'ROOT_PASSWORD_HASH=$6$salt$')"
# shellcheck disable=SC2016
expect password-hash-two-parts-REFUSES       refuse  ""       "$ONE" "$(conf 'ROOT_PASSWORD_HASH=$6$salthash')"
# shellcheck disable=SC2016
expect password-hash-rounds-form-ok          install /dev/vda "$ONE" "$(conf 'ROOT_PASSWORD_HASH=$6$rounds=5000$salt$hashvalue.A/b')"
# review r13: storage that is not TYPE=disk (md raid, multipath, lvm, crypt) at the top level counts as another storage
# device: with no explicit target the unattended default must refuse (it cannot tell what is behind it)
OTHER=$WORK/other.lsblk; { disk vda 0 0 20; printf 'NAME="md0" TYPE="raid1" ROTA="0" RM="0" SIZE="%s" TRAN=""\n' "$((20 * GIB))"; } > "$OTHER"
MPATH=$WORK/mpath.lsblk; { disk vda 0 0 20; printf 'NAME="mpatha" TYPE="mpath" ROTA="0" RM="0" SIZE="%s" TRAN=""\n' "$((20 * GIB))"; } > "$MPATH"
LVMS=$WORK/lvm.lsblk; { disk vda 0 0 20; printf 'NAME="vg-root" TYPE="lvm" ROTA="0" RM="0" SIZE="%s" TRAN=""\n' "$((20 * GIB))"; } > "$LVMS"
expect raid-device-present-REFUSES           refuse  ""       "$OTHER" "$(conf 'HOSTNAME=h1')"
expect mpath-device-present-REFUSES          refuse  ""       "$MPATH" "$(conf 'HOSTNAME=h1')"
expect lvm-device-present-REFUSES            refuse  ""       "$LVMS" "$(conf 'HOSTNAME=h1')"
expect raid-device-present-no-config         refuse  ""       "$OTHER" none
expect raid-device-explicit-target-ok        install /dev/vda "$OTHER" "$(conf 'TARGET_DISK=/dev/vda')"
expect loop-and-rom-still-do-not-count       install /dev/vda "$WORK/loop.lsblk" "$(conf 'HOSTNAME=h1')"
# review r14: `lsblk -d` (the main listing) shows ONLY top-level devices, so a raid/lvm/crypt/mpath device stacked on a
# disk never appears in it. dr-autoinstall therefore passes a second, FULL listing (no -d) with --lsblk-full, and only
# that one is searched for other storage. These fixtures are what the real tools print: -d shows the disk alone.
FULLMD=$WORK/full-md.lsblk
{ disk vda 0 0 20; printf 'NAME="vda1" TYPE="part" ROTA="0" RM="0" SIZE="%s" TRAN=""\nNAME="md0" TYPE="raid1" ROTA="0" RM="0" SIZE="%s" TRAN=""\n' "$GIB" "$((19 * GIB))"; } > "$FULLMD"
FULLLVM=$WORK/full-lvm.lsblk
{ disk vda 0 0 20; printf 'NAME="vda2" TYPE="part" ROTA="0" RM="0" SIZE="%s" TRAN=""\nNAME="vg-root" TYPE="lvm" ROTA="0" RM="0" SIZE="%s" TRAN=""\n' "$((19 * GIB))" "$((18 * GIB))"; } > "$FULLLVM"
FULLPLAIN=$WORK/full-plain.lsblk
{ disk vda 0 0 20; printf 'NAME="vda1" TYPE="part" ROTA="0" RM="0" SIZE="%s" TRAN=""\nNAME="loop0" TYPE="loop" ROTA="0" RM="0" SIZE="%s" TRAN=""\nNAME="sr0" TYPE="rom" ROTA="1" RM="1" SIZE="%s" TRAN="sata"\n' "$GIB" "$GIB" "$((2 * GIB))"; } > "$FULLPLAIN"
expect full-listing-raid-REFUSES             refuse  ""       "$ONE" "$(conf 'HOSTNAME=h1')" --lsblk-full "$FULLMD"
expect full-listing-lvm-REFUSES              refuse  ""       "$ONE" "$(conf 'HOSTNAME=h1')" --lsblk-full "$FULLLVM"
expect full-listing-raid-no-config-REFUSES   refuse  ""       "$ONE" none --lsblk-full "$FULLMD"
expect full-listing-plain-live-system-ok     install /dev/vda "$ONE" "$(conf 'HOSTNAME=h1')" --lsblk-full "$FULLPLAIN"
expect full-listing-raid-explicit-target-ok  install /dev/vda "$ONE" "$(conf 'TARGET_DISK=/dev/vda')" --lsblk-full "$FULLMD"
expect full-listing-missing-file-REFUSES     refuse  ""       "$ONE" "$(conf 'HOSTNAME=h1')" --lsblk-full "$WORK/does-not-exist"
# review r15: a full listing that cannot be trusted must refuse (fail closed), not count as "no other storage"
: > "$WORK/empty.lsblk"; printf 'this is not lsblk output\n' > "$WORK/garbage.lsblk"
expect full-listing-directory-REFUSES        refuse  ""       "$ONE" "$(conf 'HOSTNAME=h1')" --lsblk-full "$WORK"
expect full-listing-empty-file-REFUSES       refuse  ""       "$ONE" "$(conf 'HOSTNAME=h1')" --lsblk-full "$WORK/empty.lsblk"
expect full-listing-garbage-REFUSES          refuse  ""       "$ONE" "$(conf 'HOSTNAME=h1')" --lsblk-full "$WORK/garbage.lsblk"
# review r15: storage stacked ONLY on an excluded disk (a crypt device on the config stick) is not "other storage" of the
# host; the full listing carries PKNAME so descendants of excluded disks are ignored. A raid that also sits on a
# non-excluded disk still counts.
pk() { printf 'NAME="%s" TYPE="%s" ROTA="0" RM="0" SIZE="%s" TRAN="" PKNAME="%s"\n' "$1" "$2" "$((1 * GIB))" "$3"; }
FULLSTICK=$WORK/full-stick.lsblk
{ disk vda 0 0 20; pk vda1 part vda; disk sdb 0 1 16 usb; pk sdb1 part sdb; pk luks-sdb1 crypt sdb1; } > "$FULLSTICK"
FULLSPAN=$WORK/full-span.lsblk
{ disk vda 0 0 20; pk vda1 part vda; disk sdb 0 1 16 usb; pk sdb1 part sdb; pk md0 raid1 vda1; pk md0 raid1 sdb1; } > "$FULLSPAN"
expect crypt-on-excluded-stick-is-ignored    install /dev/vda "$USB" "$(conf 'HOSTNAME=h1')" --exclude sdb --lsblk-full "$FULLSTICK"
expect raid-spanning-excluded-and-other-REFUSES refuse "" "$USB" "$(conf 'HOSTNAME=h1')" --exclude sdb --lsblk-full "$FULLSPAN"
# first-ssd is rule-based, not a named disk: it must not bypass the array check either
expect first-ssd-with-raid-REFUSES           refuse  ""       "$ONE" "$(conf 'TARGET_DISK=first-ssd')" --lsblk-full "$FULLMD"
expect first-ssd-plain-ok                    install /dev/vda "$ONE" "$(conf 'TARGET_DISK=first-ssd')" --lsblk-full "$FULLPLAIN"
# shellcheck disable=SC2016
expect password-and-hash-REFUSES             refuse  ""       "$ONE" "$(conf 'ROOT_PASSWORD=abc' 'ROOT_PASSWORD_HASH=$6$salt$hash')"
# REINSTALL=yes is a known key (the runner reads it; dr-plan only validates it)
expect reinstall-key-known                   install /dev/vda "$ONE" "$(conf 'REINSTALL=yes')"
expect reinstall-bad-value-REFUSES           refuse  ""       "$ONE" "$(conf 'REINSTALL=maybe')"
# --- no config at all: never unattended, at most a typed confirmation
expect no-config-single-disk-confirms        confirm /dev/vda "$ONE" none
expect no-config-two-disks-REFUSES           refuse  ""       "$TWO" none
# --- config hygiene
expect unknown-key-REFUSES                   refuse  ""       "$ONE" "$(conf 'TARGET_DSIK=/dev/vda')"
# shellcheck disable=SC2016  # the value must reach the config file literally, unexpanded
expect bad-value-char-REFUSES                refuse  ""       "$ONE" "$(conf 'HOSTNAME=a b$(id)')"
expect comment-and-blank-lines-ok            install /dev/vda "$ONE" "$(conf '# a comment' '' 'HOSTNAME=h1')"
expect duplicate-key-REFUSES                 refuse  ""       "$TWO" "$(conf 'TARGET_DISK=/dev/vda' 'TARGET_DISK=/dev/vdb')"

# the emitted settings come from the validated config only
out=$("$PLAN" --lsblk "$ONE" --config "$(conf 'HOSTNAME=h1' 'MIRROR=http://m.example/debian')" 2>/dev/null) || true
if ! { grep -qx 'HOSTNAME=h1' <<<"$out" && grep -qx 'MIRROR=http://m.example/debian' <<<"$out"; }; then
  echo "FAIL settings-are-emitted: $out"; fail=1
fi

# --check-config (used by build-iso.sh): validates the file only, never prints a password
out=$("$PLAN" --check-config --config "$(conf 'TARGET_DISK=/dev/vda' 'ROOT_PASSWORD=s3cret')" 2>/dev/null); rc=$?
if [ "$rc" != 0 ] || ! grep -qx 'CONFIG=ok' <<<"$out" || grep -q s3cret <<<"$out"; then echo "FAIL check-config-valid: rc=$rc $out"; fail=1; fi
"$PLAN" --check-config --config "$(conf 'NOPE=1')" >/dev/null 2>&1; rc=$?
[ "$rc" = 3 ] || { echo "FAIL check-config-unknown-key: rc=$rc"; fail=1; }

[ "$fail" = 0 ] && echo "test-dr-plan: OK"
exit "$fail"
