#!/bin/sh
exec >/out/u0-e6.log 2>&1
G="git -c user.email=a@b -c user.name=x"
mkdir -p /tmp/wsr && cp -r /work/ws/. /tmp/wsr/ && rm -rf /tmp/wsr/target && cd /tmp/wsr
cargo generate-lockfile --offline >/dev/null 2>&1; $G init -q . && $G add -A && $G commit -q -m v1
try() { # name
  git diff HEAD > /tmp/$1.diff; echo "== $1: $(grep '^+++' /tmp/$1.diff | tr '\n' ' ')"
  echo "   matching mutants: $(cargo mutants --list --in-diff /tmp/$1.diff 2>&1 | grep -c 'src/lib.rs')"; git checkout -q -- .
}
sed -i 's/a::double(a::double(x))/a::double(x) + a::double(x)/' b/src/lib.rs; try b-edit1
printf '\npub fn extra(x: i32) -> i32 {\n    x - 1\n}\n' >> b/src/lib.rs; try b-append
sed -i 's/x \* 2/x * 3/' a/src/lib.rs; try a-edit
sed -i 's/x \* 2/x * 3/' a/src/lib.rs; sed -i 's/a::double(a::double(x))/a::double(x) + a::double(x)/' b/src/lib.rs; try a-and-b
echo "== which files does the mutant list name, with and without diff (a-and-b)"; sed -i 's/x \* 2/x * 3/' a/src/lib.rs; sed -i 's/a::double(a::double(x))/a::double(x) + a::double(x)/' b/src/lib.rs; git diff HEAD > /tmp/ab.diff
cargo mutants --list --in-diff /tmp/ab.diff 2>&1 | cut -d: -f1 | sort | uniq -c
