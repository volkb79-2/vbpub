#!/usr/bin/env bash
# Pins the hardening flags of dr-qemu.sh (copied from vm-lab's docker run argv) so a later
# edit cannot drop one silently. Uses --dry-run only: no docker, no QEMU needed.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fail=0
check() { # check NAME PATTERN (fixed string, one whole argv line)
  if ! printf '%s\n' "$OUT" | grep -qxF -- "$2"; then echo "FAIL $1: missing argv line '$2'"; fail=1; fi
}
OUT=$("$HERE/dr-qemu.sh" start --dry-run --name drb1 --state-dir /s --image img:1 --network dr-poc-net \
  --cgroup-parent dev-gates.slice --memory-mib 2048 --cpus 2 --ovmf-code /c --ovmf-vars /v \
  --base-raw /b.raw --seed-iso /seed.iso --data-disk-size 8589934592)
check init --init
check detach --detach
check capdrop ALL
check nnp no-new-privileges
check readonly --read-only
check tmpfs /tmp:rw,noexec,nosuid,size=64m
check pids 128
check slice dev-gates.slice
check network dr-poc-net
check cpus 2
check shares 64
check mem 2684354560
check name drb1
check image img:1
mem_lines=$(printf '%s\n' "$OUT" | grep -cxF 2684354560)
[ "$mem_lines" = 2 ] || { echo "FAIL memory-swap must equal memory (found $mem_lines lines)"; fail=1; }
printf '%s\n' "$OUT" | grep -qE -- '^--user$' || { echo "FAIL --user"; fail=1; }
printf '%s\n' "$OUT" | grep -q -- '--publish\|^-p$' && { echo "FAIL no port may be published"; fail=1; }
printf '%s\n' "$OUT" | grep -q 'cap-add\|--privileged' && { echo "FAIL privileged flags"; fail=1; }
check qemu-machine "  -name \"\$VM_NAME\" -machine q35,accel=tcg -cpu max,-vmx,-svm \\"
for bad in "--memory-mib 100" "--cpus 9"; do
  # shellcheck disable=SC2086
  if "$HERE/dr-qemu.sh" start --dry-run --name drb1 --state-dir /s --image i --network n --cgroup-parent s \
      --memory-mib 2048 --cpus 2 --ovmf-code /c --ovmf-vars /v --base-raw /b $bad >/dev/null 2>&1; then
    echo "FAIL accepted bad option: $bad"; fail=1
  fi
done
[ "$fail" = 0 ] && echo "test-dr-qemu: OK"
exit "$fail"
