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
expect explicit-target-empty-value-is-unset  refuse  ""       "$TWO" "$(conf 'TARGET_DISK=')"
# --- the config stick / mounted disks are excluded by the caller, which can leave exactly one disk
expect config-stick-excluded-leaves-one      install /dev/vda "$USB" "$(conf 'HOSTNAME=h1')"
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
