#!/bin/sh
exec >/out/u0-e4.log 2>&1
G="git -c user.email=a@b -c user.name=x"
mkdir -p /tmp/wsr && cp -r /work/ws/. /tmp/wsr/ && rm -rf /tmp/wsr/target && cd /tmp/wsr
cargo generate-lockfile --offline >/dev/null 2>&1; $G init -q . && $G add -A && $G commit -q -m v1
sed -i 's/a::double(a::double(x))/a::double(a::double(x)) + 0/' b/src/lib.rs && $G commit -qam v2
for v in "default:" "noprefix:--no-prefix" "ab2:--src-prefix=a/ --dst-prefix=b/" "x:--src-prefix=x/ --dst-prefix=y/"; do
  n=${v%%:*}; o=${v#*:}; git diff $o HEAD~1 HEAD > /tmp/$n.diff; echo "== diff $n: $(grep '^+++' /tmp/$n.diff)"
  for d in /tmp/wsr /tmp/wsr/b; do cd $d; c=$(cargo mutants --list --in-diff /tmp/$n.diff 2>&1 | grep -c "src/lib.rs"); echo "   cwd=$d matching mutants=$c"; done
done
cd /tmp/wsr; git diff --relative=b HEAD~1 HEAD > /tmp/rel.diff; echo "== diff relative-to-b: $(grep '^+++' /tmp/rel.diff)"
for d in /tmp/wsr /tmp/wsr/b; do cd $d; c=$(cargo mutants --list --in-diff /tmp/rel.diff 2>&1 | grep -c "src/lib.rs"); echo "   cwd=$d matching mutants=$c"; done
echo "== control: single crate at /work/subject-v2 default diff"; cd /work/subject-v2; echo "   $(cargo mutants --list --in-diff /work/diffs/v2.diff 2>&1 | grep -c 'src/lib.rs')"
