#!/bin/sh
exec >/out/u0-e3.log 2>&1
cd /tmp/wsr 2>/dev/null || { echo "no /tmp/wsr (fresh container)"; 
G="git -c user.email=a@b -c user.name=x"
mkdir -p /tmp/wsr && cp -r /work/ws/. /tmp/wsr/ && rm -rf /tmp/wsr/target && cd /tmp/wsr
cargo generate-lockfile --offline >/dev/null 2>&1; $G init -q . && $G add -A && $G commit -q -m v1
sed -i 's/a::double(a::double(x))/a::double(a::double(x)) + 0/' b/src/lib.rs && $G commit -qam v2; }
cat b/src/lib.rs | head -4; echo "-- list (no diff), cwd ws root:"; cargo mutants --list 2>&1 | head -12
git diff HEAD~1 HEAD > /tmp/top.diff; echo "-- diff:"; cat /tmp/top.diff
echo "-- list with diff:"; cargo mutants --list --in-diff /tmp/top.diff 2>&1 | head -8
echo "-- list with diff, -d crate dir b:"; cargo mutants --list --in-diff /tmp/top.diff -d b 2>&1 | head -8
echo "-- ws2: single-crate-in-subdir variant (control, known to work) for comparison"
cd /work/subject-v2 && cargo mutants --list --in-diff /work/diffs/v2.diff 2>&1 | head -3
