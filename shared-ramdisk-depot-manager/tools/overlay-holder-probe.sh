#!/bin/bash
# Does srdm's teardown guard still see a holder when the consumer holds an
# OVERLAY whose lowerdir is the generation, rather than a bind of it?
#
# internal/consumer matches on the SUPERBLOCK (major:minor), because a
# consumer's bind path is unpredictable while the device number is the same
# on both sides of a bind (D-012/D-018). An overlay is not a bind. If the
# overlay mount reports its own device and does not name the lower device
# anywhere mountinfo looks, the guard goes BLIND and teardown would report
# "nothing is holding this" while a server is reading it — a silent leak,
# which is the exact failure D-018 exists to prevent.

set -u
hr() { printf '\n=== %s ===\n' "$*"; }

mkdir -p /t
mount -t tmpfs tmpfs /t              # a real fs, so upperdir is usable
mkdir -p /t/{gen,upper,work,merged,expose}
mount -t tmpfs tmpfs /t/gen          # stands in for the class op tmpfs
mkdir -p /t/gen/root
echo payload > /t/gen/root/game.pak
mount --bind /t/gen/root /t/expose   # publication's bind
mount -o remount,ro,bind /t/expose   # ...made read-only

# Linux packs st_dev as 20:12, not 8:8.
gen_dev=$(stat -c '%d' /t/gen/root/game.pak)
maj=$(( (gen_dev >> 8) & 0xfff ))
min=$(( (gen_dev & 0xff) | ((gen_dev >> 12) & 0xfff00) ))
printf 'generation tmpfs st_dev=%s -> %d:%d\n' "$gen_dev" "$maj" "$min"

hr "how the RO bind appears in mountinfo (what the guard sees TODAY)"
grep ' /t/expose ' /proc/self/mountinfo

mount -t overlay overlay \
  -o lowerdir=/t/expose,upperdir=/t/upper,workdir=/t/work /t/merged \
  || { echo "overlay mount failed"; exit 1; }

hr "how the OVERLAY appears in mountinfo (what the guard would have to see)"
grep ' /t/merged ' /proc/self/mountinfo

hr "device numbers, compared"
echo "file read through the overlay: st_dev=$(stat -c '%d' /t/merged/game.pak)"
echo "same file via the RO bind    : st_dev=$(stat -c '%d' /t/expose/game.pak)"
echo "same file in the generation  : st_dev=$(stat -c '%d' /t/gen/root/game.pak)"

hr "does the lower device number appear ANYWHERE in the overlay's mountinfo line?"
line=$(grep ' /t/merged ' /proc/self/mountinfo)
if printf '%s' "$line" | grep -q "$maj:$min"; then
  echo "YES — the guard could match on it"
else
  echo "NO — matching on major:minor alone CANNOT see this holder."
  echo "   the only trace of the lower is the lowerdir= PATH in the options:"
  printf '%s\n' "$line" | tr ' ' '\n' | grep -i 'lowerdir\|upperdir\|workdir' || true
fi

hr "and in a SEPARATE mount namespace (how a container would look)"
unshare --mount --propagation slave bash -c '
  echo "--- overlay as seen from the other namespace ---"
  grep " /t/merged " /proc/self/mountinfo || echo "(overlay not visible)"
  echo "--- can it still read the content? ---"
  cat /t/merged/game.pak 2>&1
'

hr "the decisive one: unmount the generation while the overlay holds it"
umount /t/expose 2>&1 && echo "RO bind unmounted OK" || echo "RO bind unmount REFUSED"
umount /t/gen    2>&1 && echo "generation tmpfs unmounted OK" || echo "generation tmpfs unmount REFUSED (EBUSY)"
echo "content still readable through the overlay afterwards?"
cat /t/merged/game.pak 2>&1 || echo "(unreadable — the unmount took it away)"

umount /t/merged 2>/dev/null
umount /t/gen 2>/dev/null
hr done
