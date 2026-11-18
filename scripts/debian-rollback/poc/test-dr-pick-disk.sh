#!/usr/bin/env bash
# Tests dr-pick-disk against canned `lsblk -dnbP` output. No devices touched.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PICK="$HERE/dr-pick-disk"
fail=0
G=$((1024 * 1024 * 1024))
row() { printf 'NAME="%s" TYPE="%s" ROTA="%s" RM="%s" SIZE="%s" TRAN="%s"\n' "$1" "$2" "$3" "$4" "$((${5} * G))" "$6"; }
expect() { # expect LABEL WANT INPUT [args...]
  local label=$1 want=$2 input=$3; shift 3
  local got; got=$(printf '%s' "$input" | "$PICK" "$@" 2>/dev/null) || got="none"
  if [ "$got" != "$want" ]; then echo "FAIL $label: want '$want' got '$got'"; fail=1; fi
}

# SSD wins over an earlier-named rotational disk
expect ssd-before-hdd /dev/sdb "$(row sda disk 1 0 4000 sata; row sdb disk 0 0 500 sata)"
# NVMe wins over a SATA SSD
expect nvme-first /dev/nvme0n1 "$(row sda disk 0 0 500 sata; row nvme0n1 disk 0 0 1000 nvme)"
# natural order: sda before sdb before sdc10
expect natural-order /dev/sda "$(row sdb disk 0 0 500 sata; row sdc10 disk 0 0 500 sata; row sda disk 0 0 500 sata)"
# fallback: only rotational disks, first by name
expect hdd-fallback /dev/sda "$(row sdb disk 1 0 4000 sata; row sda disk 1 0 2000 sata)"
# USB-attached and removable devices are never chosen, even if non-rotational
expect no-usb none "$(row sda disk 0 0 64 usb)"
expect no-removable none "$(row sda disk 0 1 64 sata)"
# the disk holding the booted ISO is excluded
expect exclude /dev/sdb "$(row sda disk 0 0 500 sata; row sdb disk 0 0 500 sata)" --exclude sda
# too small
expect too-small none "$(row vda disk 0 0 4 virtio)"
expect min-gib-override /dev/vda "$(row vda disk 0 0 4 virtio)" --min-gib 2
# partitions, loop, rom are ignored
expect only-disks /dev/vda "$(row loop0 loop 0 0 8 ''; row sr0 rom 1 0 2 sata; row vda disk 0 0 20 virtio)"
# empty input
expect empty none ""
# hostile name is skipped, not evaluated
# shellcheck disable=SC2016  # the single quotes are the point: a literal $(...) in the device name
expect hostile /dev/vda "$(printf 'NAME="$(touch /tmp/pwned)" TYPE="disk" ROTA="0" RM="0" SIZE="%s" TRAN=""\n' "$((20 * G))"; row vda disk 0 0 20 virtio)"
[ ! -e /tmp/pwned ] || { echo "FAIL hostile name was evaluated"; fail=1; rm -f /tmp/pwned; }

[ "$fail" = 0 ] && echo "test-dr-pick-disk: OK"
exit "$fail"
