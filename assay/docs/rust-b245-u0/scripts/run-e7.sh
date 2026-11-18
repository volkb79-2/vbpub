#!/bin/sh
exec >/out/u0-e7.log 2>&1
G="git -c user.email=a@b -c user.name=x"
mkdir -p /tmp/w2 && cp -r /work/ws/. /tmp/w2/ && cd /tmp/w2 && rm -rf target
mv a crate_a && mv b crate_b
sed -i 's/"a", "b"/"crate_a", "crate_b"/' Cargo.toml
sed -i 's/name = "a"/name = "crate_a"/' crate_a/Cargo.toml
sed -i 's/name = "b"/name = "crate_b"/; s/^a = { path = "..\/a" }/crate_a = { path = "..\/crate_a" }/' crate_b/Cargo.toml
sed -i 's/a::double/crate_a::double/g' crate_b/src/lib.rs
cat Cargo.toml; cargo generate-lockfile --offline >/dev/null 2>&1; $G init -q . && $G add -A && $G commit -q -m v1
try() { git diff HEAD > /tmp/$1.diff; echo "== $1: $(grep '^+++' /tmp/$1.diff | tr '\n' ' ')"; echo "   matching mutants: $(cargo mutants --list --in-diff /tmp/$1.diff 2>&1 | grep -c 'src/lib.rs')"; git checkout -q -- .; }
sed -i 's/crate_a::double(crate_a::double(x))/crate_a::double(x) + crate_a::double(x)/' crate_b/src/lib.rs; try crate_b-edit
sed -i 's/x \* 2/x * 3/' crate_a/src/lib.rs; try crate_a-edit
echo "== from crate_b dir with a diff relative to the workspace root (default git diff), and relative to the crate"
sed -i 's/crate_a::double(crate_a::double(x))/crate_a::double(x) + crate_a::double(x)/' crate_b/src/lib.rs; git diff HEAD > /tmp/top.diff; git diff --relative=crate_b HEAD > /tmp/rel.diff; git checkout -q -- .
cd crate_b; echo "   cwd=crate_b, top diff: $(cargo mutants --list --in-diff /tmp/top.diff 2>&1 | grep -c 'src/lib.rs')  rel diff: $(cargo mutants --list --in-diff /tmp/rel.diff 2>&1 | grep -c 'src/lib.rs')"
