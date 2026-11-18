#!/bin/sh
# B245 U0 capture (v2, 2026-11-18): regenerates every fixture case of tests/fixtures/rust/cargo-mutants and the
# measurements of docs/RUST-B245-U0-MEASUREMENTS.md. Runs inside tester-unified-rust:local, --network none.
#   /work = the directory docs/rust-b245-u0/inputs (read-only inputs); /out = results.
# Every tool exit code is captured into a variable BEFORE any pipe (a pipeline's status is the last command's),
# written to <case>/exit-code.txt and to /out/summary.txt. Per case the fixture keeps subject/ (the sources the run
# used) and the whole mutants.out except lock.json, debug.log and log/ (host identifiers / per-mutant logs).
exec >/out/u0.txt 2>&1
set -u
G="git -c user.email=a@b -c user.name=x"
BASE="--test-tool nextest --jobs 1 --timeout 20 --cargo-arg=--offline"
LF="$BASE --cargo-arg=--locked"
SUM=/out/summary.txt; : > $SUM
echo "cargo-mutants $(cargo mutants --version | cut -d' ' -f2) rustc $(rustc --version | cut -d' ' -f2) nextest $(cargo nextest --version | head -1 | cut -d' ' -f2)" | tee -a $SUM

# run <label> <cwd> <args...>: runs cargo mutants, records its REAL exit code, prints the last lines.
run() {
  label=$1; dir=$2; shift 2
  ( cd "$dir" && cargo mutants "$@" > "/tmp/run-$label.txt" 2>&1; echo $? > "/tmp/rc-$label.txt" )
  rc=$(cat "/tmp/rc-$label.txt"); tail -3 "/tmp/run-$label.txt"
  echo "$label exit=$rc" | tee -a $SUM
}
lockit() { (cd "$1" && cargo generate-lockfile --offline >/dev/null 2>&1); }
# keep <case> <subject-dir> <out-dir> <exit-label>
keep() {
  c=$1; d=/out/cases/$c; rm -rf "$d"; mkdir -p "$d/subject"
  (cd "$2" && tar -cf - --exclude=target --exclude=.git --exclude='out*' --exclude=mutants.out --exclude=mutants.out.old .) | tar -xf - -C "$d/subject"
  for m in mutants.out mutants.out.old; do
    [ -d "$3/$m" ] && cp -r "$3/$m" "$d/$m" && rm -rf "$d/$m/lock.json" "$d/$m/debug.log" "$d/$m/log"
  done
  [ -f "/tmp/rc-$4.txt" ] && cp "/tmp/rc-$4.txt" "$d/exit-code.txt"
}
# repo <dir>: git-init <dir> with everything committed as v1
repo() { (cd "$1" && $G init -q . && $G add -A && $G commit -q -m v1); }

echo "##### CASES (D8 flags: nextest, --locked --offline)"
rm -rf /tmp/t && mkdir -p /tmp/t && cd /tmp/t
cp -r /work/subject full && lockit full && run full full $LF --output /tmp/t/o-full; keep full /tmp/t/full /tmp/t/o-full full
cp -r /work/subject-bf basefail-test && lockit basefail-test && run basefail-test basefail-test $LF --output /tmp/t/o-bt; keep basefail-test /tmp/t/basefail-test /tmp/t/o-bt basefail-test
cp -r /work/subject basefail-build && run basefail-build basefail-build $LF --output /tmp/t/o-bb; keep basefail-build /tmp/t/basefail-build /tmp/t/o-bb basefail-build   # no Cargo.lock on purpose
# indiff / zero-mutants: REAL git diffs (index line, hunk context) between two commits
cp -r /work/subject indiff && lockit indiff && repo indiff && cp /work/subject-v2/src/lib.rs indiff/src/lib.rs && (cd indiff && $G commit -qam v2 && git diff HEAD~1 HEAD > /tmp/t/indiff.diff)
run indiff indiff $LF --in-diff /tmp/t/indiff.diff --output /tmp/t/o-indiff; keep indiff /tmp/t/indiff /tmp/t/o-indiff indiff; cp /tmp/t/indiff.diff /out/cases/indiff/in.diff
cp -r /work/subject zero-mutants && lockit zero-mutants && repo zero-mutants && cp /work/subject-cm/src/lib.rs zero-mutants/src/lib.rs && (cd zero-mutants && $G commit -qam cm && git diff HEAD~1 HEAD > /tmp/t/zero.diff)
run zero-mutants zero-mutants $LF --in-diff /tmp/t/zero.diff --output /tmp/t/o-zero; keep zero-mutants /tmp/t/zero-mutants /tmp/t/o-zero zero-mutants; cp /tmp/t/zero.diff /out/cases/zero-mutants/in.diff
ls /tmp/t/o-zero > /out/cases/zero-mutants/output-dir-listing.txt 2>&1   # records that --output was never created
cp -r /work/subject-v2 rotation && lockit rotation
run rotation-1 rotation $LF --in-diff /tmp/t/indiff.diff --output /tmp/t/o-rot; run rotation-2 rotation $LF --in-diff /tmp/t/indiff.diff --output /tmp/t/o-rot; ls /tmp/t/o-rot | tee -a $SUM; keep rotation /tmp/t/rotation /tmp/t/o-rot rotation-2
for tw in unset true; do
  cp -r /work/ws w-$tw && lockit w-$tw; A=""; [ $tw = true ] && A="--test-workspace=true"
  run w-$tw w-$tw $LF $A --output /tmp/t/o-w-$tw; n=crashed-f4; [ $tw = true ] && n=w-true; keep $n /tmp/t/w-$tw /tmp/t/o-w-$tw w-$tw
done
mkdir -p unicode/src && cp /work/subject/Cargo.toml unicode/ && cat > unicode/src/lib.rs <<'RS'
pub fn uni(a: usize) -> usize {
    let s = "héllo→"; s.len() + a
}

#[cfg(test)]
mod tests {
    #[test]
    fn t() {
        assert_eq!(super::uni(1), 10);
    }
}
RS
lockit unicode && run unicode unicode $LF --output /tmp/t/o-uni; keep unicode /tmp/t/unicode /tmp/t/o-uni unicode
cp -r /work/paths paths && lockit paths && run paths paths $LF --output /tmp/t/o-paths; ls /tmp/t/o-paths/mutants.out/diff | tee -a $SUM; keep paths /tmp/t/paths /tmp/t/o-paths paths
cp -r /work/methods methods && lockit methods && (cd methods && cargo mutants --list --json > /tmp/t/methods-list.json); python3 -c "
import json;d=json.load(open('/tmp/t/methods-list.json'))
for n in sorted({(m['genre'], m['function']['function_name']) for m in d}): print('function_name', n)" | tee -a $SUM
run methods methods $LF --output /tmp/t/o-methods; keep methods /tmp/t/methods /tmp/t/o-methods methods

echo "##### subdir case: crate in sub/crate1 of a git repo, relative in-tree --output, real git diffs"
rm -rf /tmp/repo && mkdir -p /tmp/repo/other /tmp/repo/sub/crate1 && echo hi > /tmp/repo/other/README
cp -r /work/subject/Cargo.toml /work/subject/src /tmp/repo/sub/crate1/ && lockit /tmp/repo/sub/crate1 && repo /tmp/repo
cp /work/subject-v2/src/lib.rs /tmp/repo/sub/crate1/src/lib.rs && (cd /tmp/repo && $G commit -qam v2 && git diff --relative=sub/crate1 HEAD~1 HEAD > /tmp/t/subdir-relative.diff && git diff HEAD~1 HEAD > /tmp/t/subdir-toplevel.diff)
head -3 /tmp/t/subdir-relative.diff; head -3 /tmp/t/subdir-toplevel.diff
# the case: relative diff + relative in-tree --output (reserved empty beforehand, as assay will)
mkdir -p /tmp/repo/sub/crate1/out-r; run subdir-relative /tmp/repo/sub/crate1 $LF --in-diff /tmp/t/subdir-relative.diff --output out-r
ls /tmp/repo/sub/crate1/out-r | tee -a $SUM
mkdir -p /tmp/t/o-subdir && cp -r /tmp/repo/sub/crate1/out-r/mutants.out /tmp/t/o-subdir/ && keep subdir /tmp/repo/sub/crate1 /tmp/t/o-subdir subdir-relative
cp /tmp/t/subdir-relative.diff /out/cases/subdir/in.diff; cp /tmp/t/subdir-toplevel.diff /out/cases/subdir/toplevel.diff
run subdir-toplevel-from-crate /tmp/repo/sub/crate1 $LF --in-diff /tmp/t/subdir-toplevel.diff --output out-t     # wrong anchor
(cd /tmp/repo && echo "-- git status after the runs (untracked in-tree out dirs):" && git status --porcelain) | tee -a $SUM

echo "##### (b) stop gate: is the in-tree <out> copied into the mutant tree? Controls included."
# canary <name> <setup> <asserted-path-relative-to-manifest-dir>: a crate whose only test fails when <path> exists in the tree under test.
canary() {
  name=$1; setup=$2; path=$3; rm -rf /tmp/cn-$name && mkdir -p /tmp/cn-$name/src && cp /work/subject/Cargo.toml /tmp/cn-$name/ && cd /tmp/cn-$name
  cat > src/lib.rs <<RS
pub fn f(a: i32) -> i32 { a + 1 }

#[cfg(test)]
mod t {
    #[test]
    fn marker_is_not_in_the_tree_under_test() {
        let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("$path");
        assert!(!p.exists(), "COPIED: {}", p.display());
    }
}
RS
  lockit /tmp/cn-$name
  eval "$setup"; $G init -q . 2>/dev/null; $G add -A >/dev/null 2>&1; $G commit -q -m c >/dev/null 2>&1
  run canary-$name /tmp/cn-$name $LF --output out-x
  python3 -c "
import json;o=json.load(open('/tmp/cn-$name/out-x/mutants.out/outcomes.json'));b=o['outcomes'][0]
print('canary-$name baseline:',b['summary'],[(p['phase'],p['process_status']) for p in b['phase_results']],'total_mutants',o['total_mutants'])" 2>&1 | tee -a $SUM
}
# A control: test asserts a path that never exists -> must PASS (proves the test itself is sound)
canary A-control 'mkdir -p out-x' 'never-exists'
# B non-empty untracked <out> holding a marker file -> copied?
canary B-nonempty-marker 'mkdir -p out-x && echo m > out-x/MARKER' 'out-x/MARKER'
# C EMPTY pre-created <out> (what assay will create): is the directory itself, or the mutants.out the tool creates inside it, copied?
canary C-empty-out-mutants-out 'mkdir -p out-x' 'out-x/mutants.out'
# D <out> ignored by a committed .gitignore (what the dirty-tree check requires) with a marker: copied?
canary D-gitignored-marker 'mkdir -p out-x && echo m > out-x/MARKER && printf "out-x/\n" > .gitignore' 'out-x/MARKER'
# E same as D, but is the ignored directory's own mutants.out copied?
canary E-gitignored-empty-mutants-out 'mkdir -p out-x && printf "out-x/\n" > .gitignore' 'out-x/mutants.out'

echo "##### (d) NON-empty pre-created --output with a real run"
cd /tmp/repo/sub/crate1 && mkdir -p out-d && echo keep > out-d/keepme
run nonempty-out /tmp/repo/sub/crate1 $LF --in-diff /tmp/t/subdir-relative.diff --output out-d; ls out-d | tee -a $SUM

echo "##### (e, h) in-diff anchoring matrix: git top = cargo workspace root; names of directory and package varied independently"
# mkws <name> <dirB> <pkgB>: two-crate workspace; crate A is always dir+package `crate_a`; crate B (dir <dirB>, package <pkgB>)
# depends on A and has the only test; the diff changes crate B's source. Only B's directory and package names vary.
mkws() {
  w=/tmp/ws-$1; rm -rf $w && mkdir -p $w/crate_a/src $w/$2/src && cd $w
  printf '[workspace]\nmembers = ["crate_a", "%s"]\nresolver = "2"\n' "$2" > Cargo.toml
  printf '[package]\nname = "crate_a"\nversion = "0.1.0"\nedition = "2021"\n' > crate_a/Cargo.toml
  printf 'pub fn double(x: i32) -> i32 {\n    x * 2\n}\n' > crate_a/src/lib.rs
  printf '[package]\nname = "%s"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\ncrate_a = { path = "../crate_a" }\n' "$3" > $2/Cargo.toml
  printf 'pub fn quad(x: i32) -> i32 {\n    crate_a::double(crate_a::double(x))\n}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn q() {\n        assert_eq!(super::quad(3), 12);\n    }\n}\n' > $2/src/lib.rs
  lockit $w; repo $w
  printf 'pub fn quad(x: i32) -> i32 {\n    crate_a::double(x) + crate_a::double(x)\n}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn q() {\n        assert_eq!(super::quad(3), 12);\n    }\n}\n' > $2/src/lib.rs
  git diff > /tmp/ws-$1.diff
  for cwd in $w $w/$2; do
    ( cd $cwd && cargo mutants --list --in-diff /tmp/ws-$1.diff > /tmp/l.txt 2>&1; rc=$?
      echo "matrix $1 (dirB=$2 pkgB=$3) cwd=${cwd#/tmp/ws-$1} matched=$(grep -c 'src/lib.rs' /tmp/l.txt) first=[$(head -1 /tmp/l.txt | cut -c1-70)] exit=$rc" | tee -a $SUM )
  done
}
mkws dirb-pkgb b b
mkws dirb-pkgcb b crate_b
mkws dircb-pkgb crate_b b
mkws dircb-pkgcb crate_b crate_b
mkws dirb2-pkgb2 b2 b2
echo "##### (e2) REAL mutation run from the member directory, diff anchored at the workspace root vs at the member"
w=/tmp/ws-dircb-pkgcb; cd $w; git diff > /tmp/top.diff; git diff --relative=crate_b > /tmp/rel.diff
run member-cwd-top-diff $w/crate_b $LF --in-diff /tmp/top.diff --output out-m1
run member-cwd-rel-diff $w/crate_b $LF --in-diff /tmp/rel.diff --output out-m2
run root-cwd-top-diff $w $LF --in-diff /tmp/top.diff --output out-m3
echo DONE > /out/DONE-U0
