#!/bin/sh
exec >/out/u0-e5.log 2>&1
G="git -c user.email=a@b -c user.name=x"
mkdir -p /tmp/wsr && cp -r /work/ws/. /tmp/wsr/ && rm -rf /tmp/wsr/target && cd /tmp/wsr
cargo generate-lockfile --offline >/dev/null 2>&1; $G init -q . && $G add -A && $G commit -q -m v1
sed -i 's/a::double(a::double(x))/a::double(a::double(x)) + 0/' b/src/lib.rs && $G commit -qam v2
git diff HEAD~1 HEAD > /tmp/d.diff
echo "== trace, ws root, default diff"; CARGO_MUTANTS_TRACE_LEVEL=trace cargo mutants --list --in-diff /tmp/d.diff -vv 2>&1 | grep -i -E "diff|filter|path|changed|overlap" | head -20
echo "== same but ONLY package b selected"; cargo mutants --list --in-diff /tmp/d.diff --package b 2>&1 | head -4
echo "== a change in crate a instead"; git checkout -q HEAD~1 -- b; sed -i 's/x \* 2/x * 2 + 0/' a/src/lib.rs; git diff HEAD > /tmp/da.diff; grep '^+++' /tmp/da.diff
cargo mutants --list --in-diff /tmp/da.diff 2>&1 | head -4
echo "== copy of the crate a alone as a single-crate project (control)"; rm -rf /tmp/solo && mkdir /tmp/solo && cp -r a/. /tmp/solo/ && cd /tmp/solo && $G init -q . && $G add -A && $G commit -q -m s1 && sed -i 's/x \* 2 + 0/x * 2 + 1/' src/lib.rs && git diff > /tmp/ds.diff && cargo mutants --list --in-diff /tmp/ds.diff 2>&1 | head -3
