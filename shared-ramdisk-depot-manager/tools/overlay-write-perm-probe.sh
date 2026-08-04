#!/bin/bash
# With a SEALED lower (root-owned, a-w — exactly what publication leaves) and
# an upperdir srdm creates itself, what does an UNPRIVILEGED uid actually need
# in order to write through the merged view, with no declared write-owner at
# all (D-029)? Does the top-level upperdir alone suffice, or does the whole
# lower directory tree have to be MIRRORED into the upper, permissively? And
# does a true in-place O_TRUNC on an existing lower-only file ever work
# without help?
#
# D-027 already measured that a write forces copy-up. This probe is the next
# question D-027 does not answer: once copy-up happens, who is allowed to
# TRIGGER it, and what does the enclosing directory need to look like.

set -u
hr() { printf '\n=== %s ===\n' "$*"; }

mkdir -p /t2
mount -t tmpfs tmpfs /t2
mkdir -p /t2/{lower,upper,work,merged}

mkdir -p /t2/lower/sub
echo "original" > /t2/lower/sub/existing.txt
echo "original2" > /t2/lower/keep.txt
chown -R 0:0 /t2/lower
chmod -R a-w /t2/lower
find /t2/lower -type d -exec chmod 0555 {} \;
find /t2/lower -type f -exec chmod 0444 {} \;

hr "lower tree, sealed"
find /t2/lower -exec stat -c '%A %U:%G %n' {} \;

# Upper: srdm creates and owns it, and MIRRORS the lower's directory
# structure (dirs only, never files) at a permissive mode — the way it has
# to if create/delete are meant to work anywhere below the top level.
mkdir -p /t2/upper
chmod 0777 /t2/upper
mkdir -p /t2/upper/sub
chmod 0777 /t2/upper/sub
mkdir -p /t2/work

mount -t overlay overlay \
  -o lowerdir=/t2/lower,upperdir=/t2/upper,workdir=/t2/work /t2/merged \
  || { echo "overlay mount failed"; exit 1; }

hr "merged tree before any writes (mirrored dirs)"
find /t2/merged -exec stat -c '%A %U:%G %n' {} \;

runas() { setpriv --reuid=65534 --regid=65534 --clear-groups -- "$@"; }

hr "as uid 65534: create a brand-new file at the top of merged"
runas bash -c 'echo brandnew > /t2/merged/new.txt' 2>&1
echo "result: $?"; stat -c '%A %U:%G %n' /t2/merged/new.txt 2>&1

hr "as uid 65534: create a new file inside an existing lower-only SUBDIRECTORY (upper mirrors it)"
runas bash -c 'echo sub-new > /t2/merged/sub/added.txt' 2>&1
echo "result: $?"
stat -c '%A %U:%G %n' /t2/merged/sub/added.txt 2>&1

hr "as uid 65534: unlink an existing lower-only file, then recreate it"
runas bash -c 'rm -f /t2/merged/keep.txt && echo replaced > /t2/merged/keep.txt' 2>&1
echo "result: $?"
cat /t2/merged/keep.txt 2>&1

hr "as uid 65534: TRUE in-place open(O_WRONLY|O_TRUNC) on an existing lower-only file (no unlink)"
runas bash -c '> /t2/merged/sub/existing.txt' 2>&1
echo "result: $?"
cat /t2/merged/sub/existing.txt 2>&1
stat -c '%A %U:%G %n' /t2/upper/sub/existing.txt 2>&1 || echo "(no copy-up happened)"

hr "as uid 65534: does mkdir of a NEW subdirectory work at the top level?"
runas bash -c 'mkdir /t2/merged/newdir && echo ok > /t2/merged/newdir/f.txt' 2>&1
find /t2/merged/newdir -exec stat -c '%A %U:%G %n' {} \; 2>&1

hr "did the LOWER change at all across any of this?"
find /t2/lower -exec stat -c '%A %U:%G %n' {} \;
cat /t2/lower/sub/existing.txt
cat /t2/lower/keep.txt

hr "what does the upper directory tree look like now?"
find /t2/upper -exec stat -c '%A %U:%G %n' {} \;

hr "rename() across into a dir that exists ONLY in lower, never mirrored"
mkdir -p /t2/lower/unmirrored
chmod 0555 /t2/lower/unmirrored
runas bash -c 'echo x > /t2/merged/newdir/tomove.txt; mv /t2/merged/newdir/tomove.txt /t2/merged/unmirrored/tomove.txt' 2>&1
echo "result: $?"

umount /t2/merged 2>/dev/null
hr done
