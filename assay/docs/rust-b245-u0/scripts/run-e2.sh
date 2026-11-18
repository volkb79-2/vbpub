#!/bin/sh
exec >/out/u0-e2.log 2>&1
G="git -c user.email=a@b -c user.name=x"
F="--test-tool nextest --cargo-arg=--offline --jobs 1 --timeout 20 --cargo-arg=--locked"
rm -rf /tmp/wsr && mkdir -p /tmp/wsr && cp -r /work/ws/. /tmp/wsr/ && rm -rf /tmp/wsr/target && cd /tmp/wsr
cargo generate-lockfile --offline >/dev/null 2>&1; $G init -q . && $G add -A && $G commit -q -m v1
sed -i 's/a::double(a::double(x))/a::double(a::double(x)) + 0/' b/src/lib.rs && $G commit -qam v2
git diff --relative=b HEAD~1 HEAD > /out/e2-relative-b.diff; git diff HEAD~1 HEAD > /out/e2-toplevel.diff
echo "-- toplevel diff paths:"; grep '^+++' /out/e2-toplevel.diff; echo "-- relative-to-b diff paths:"; grep '^+++' /out/e2-relative-b.diff
cd /tmp/wsr/b
echo "== cwd = crate b (workspace root above), diff relative to b"; cargo mutants $F --in-diff /out/e2-relative-b.diff --output out-1 2>&1 | grep -E "Found|No mutants|ERROR|tested|Diff" ; echo "exit=$?"
echo "== cwd = crate b, diff relative to the git top (= workspace root)"; cargo mutants $F --in-diff /out/e2-toplevel.diff --output out-2 2>&1 | grep -E "Found|No mutants|ERROR|tested|Diff"; echo "exit=$?"
cd /tmp/wsr
echo "== cwd = workspace root, diff relative to the workspace root"; cargo mutants $F --in-diff /out/e2-toplevel.diff --output out-3 2>&1 | grep -E "Found|No mutants|ERROR|tested|Diff"; echo "exit=$?"
echo "== cwd = workspace root, diff relative to b (wrong anchor)"; cargo mutants $F --in-diff /out/e2-relative-b.diff --output out-4 2>&1 | grep -E "Found|No mutants|ERROR|tested|Diff"; echo "exit=$?"
echo DONE > /out/DONE-E2
