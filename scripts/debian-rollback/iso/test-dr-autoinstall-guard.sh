#!/usr/bin/env bash
# Offline test of the re-install guard in dr-autoinstall (`dr-autoinstall _holds-system /dev/X`): exit 0 when a
# partition of the disk carries the btrfs label drroot. lsblk is replaced by a stub on PATH; nothing touches a disk.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir "$WORK/bin"
fail=0
# the stub prints the contents of $STUB_LABELS (one label per line, as `lsblk -nr -o LABEL DISK` would: the disk's own
# empty label line first, then its partitions) and records its arguments
cat >"$WORK/bin/lsblk" <<'EOF'
#!/bin/sh
echo "$@" >>"$STUB_ARGS"
cat "$STUB_LABELS"
EOF
chmod +x "$WORK/bin/lsblk"
export STUB_ARGS=$WORK/args STUB_LABELS=$WORK/labels
check() { # check LABEL WANT_RC  (labels come from STUB_LABELS)
  PATH="$WORK/bin:$PATH" "$HERE/dr-autoinstall" _holds-system /dev/vda >/dev/null 2>&1; local rc=$?
  [ "$rc" = "$2" ] || { echo "FAIL $1: want rc $2, got $rc"; fail=1; }
}
printf '\n\nEFI\ndrroot\n' >"$WORK/labels";   check "drroot-on-a-partition-is-detected" 0
printf '\nEFI\n' >"$WORK/labels";             check "no-drroot-is-clean" 1
printf '\n' >"$WORK/labels";                  check "blank-disk-is-clean" 1
printf '\ndrroot2\nmydrroot\n' >"$WORK/labels"; check "similar-labels-do-not-match" 1
printf '\nDRROOT\n' >"$WORK/labels";          check "label-match-is-exact-and-case-sensitive" 1
if ! { grep -q -- '-o LABEL' "$WORK/args" && grep -q '/dev/vda' "$WORK/args"; }; then
  echo "FAIL lsblk-called-for-the-target-with-LABEL"; fail=1
fi
[ "$fail" = 0 ] && echo "test-dr-autoinstall-guard: OK"
exit "$fail"
