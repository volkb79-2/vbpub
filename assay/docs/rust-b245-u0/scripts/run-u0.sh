#!/bin/sh
# U0: B245 fixture re-capture + design section 7.4 measurements (a)-(h).
# Inside tester-unified-rust:local, --network none. /work = inputs (subject, subject-v2, subject-bf, subject-cm, ws, paths, methods, diffs).
# /out = results. Per-case fixture = subject sources + the COMPLETE mutants.out (only lock.json and debug.log are dropped).
exec >/out/u0.log 2>&1
set -u
G="git -c user.email=a@b -c user.name=x"
F="--test-tool nextest --cargo-arg=--offline --jobs 1 --timeout 20"
echo "cargo-mutants $(cargo mutants --version) / $(rustc --version) / $(cargo nextest --version | head -1)"
mkdirs() { mkdir -p "/out/cases/$1"; }
keep() { # keep <case> <subject-dir> <out-dir>
  mkdirs "$1"; rm -rf "/out/cases/$1/subject" "/out/cases/$1/mutants.out" "/out/cases/$1/mutants.out.old"
  mkdir -p "/out/cases/$1/subject"; (cd "$2" && tar -cf - --exclude=target --exclude=.git --exclude='out*' --exclude=mutants.out .) | tar -xf - -C "/out/cases/$1/subject"
  [ -d "$3/mutants.out" ] && cp -r "$3/mutants.out" "/out/cases/$1/mutants.out"
  [ -d "$3/mutants.out.old" ] && cp -r "$3/mutants.out.old" "/out/cases/$1/mutants.out.old"
  rm -f /out/cases/$1/mutants.out/lock.json /out/cases/$1/mutants.out/debug.log /out/cases/$1/mutants.out.old/lock.json /out/cases/$1/mutants.out.old/debug.log
}
lockit() { (cd "$1" && cargo generate-lockfile --offline >/dev/null 2>&1); }
LF="$F --cargo-arg=--locked"

echo "##### CASES (D8 flags: nextest, --locked --offline)"
for c in subject:full subject-bf:basefail-test subject-cm:zero-mutants; do
  d=${c%%:*}; n=${c##*:}; rm -rf /tmp/c-$n && cp -r /work/$d /tmp/c-$n && lockit /tmp/c-$n && cd /tmp/c-$n
  A=""; [ $n = zero-mutants ] && A="--in-diff /work/diffs/comment.diff"
  echo "=== case $n"; cargo mutants $LF $A --output /tmp/o-$n 2>&1 | tail -2; echo "exit=$?"; keep $n /tmp/c-$n /tmp/o-$n
done
echo "=== case indiff"; rm -rf /tmp/c-indiff && cp -r /work/subject-v2 /tmp/c-indiff && lockit /tmp/c-indiff && cd /tmp/c-indiff
cargo mutants $LF --in-diff /work/diffs/v2.diff --output /tmp/o-indiff 2>&1 | tail -2; echo "exit=$?"; keep indiff /tmp/c-indiff /tmp/o-indiff
echo "=== case basefail-build (--locked WITHOUT a Cargo.lock)"; rm -rf /tmp/c-bb && cp -r /work/subject /tmp/c-bb && rm -f /tmp/c-bb/Cargo.lock && cd /tmp/c-bb
cargo mutants $LF --output /tmp/o-bb 2>&1 | tail -3; echo "exit=$?"; keep basefail-build /tmp/c-bb /tmp/o-bb
echo "=== case rotation (two runs into one --output)"; rm -rf /tmp/c-rot && cp -r /work/subject-v2 /tmp/c-rot && lockit /tmp/c-rot && cd /tmp/c-rot
cargo mutants $LF --in-diff /work/diffs/v2.diff --output /tmp/o-rot >/dev/null 2>&1; cargo mutants $LF --in-diff /work/diffs/v2.diff --output /tmp/o-rot >/dev/null 2>&1; ls /tmp/o-rot; keep rotation /tmp/c-rot /tmp/o-rot
echo "=== cases crashed-f4 (workspace, flag unset) and w-true"; for tw in unset true; do
  rm -rf /tmp/c-w-$tw && cp -r /work/ws /tmp/c-w-$tw && rm -rf /tmp/c-w-$tw/target && lockit /tmp/c-w-$tw && cd /tmp/c-w-$tw
  A=""; [ $tw = true ] && A="--test-workspace=true"
  cargo mutants $LF $A --output /tmp/o-w-$tw >/dev/null 2>&1; echo "tw=$tw exit=$?"
  n=crashed-f4; [ $tw = true ] && n=w-true; keep $n /tmp/c-w-$tw /tmp/o-w-$tw
done
echo "=== case unicode (non-ASCII line)"; mkdir -p /tmp/c-uni && cp -r /work/subject-v2/. /tmp/c-uni/ 2>/dev/null
rm -rf /tmp/c-uni/src && mkdir -p /tmp/c-uni/src && cat > /tmp/c-uni/src/lib.rs <<'RS'
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
lockit /tmp/c-uni && cd /tmp/c-uni && cargo mutants $LF --output /tmp/o-uni 2>&1 | tail -1; keep unicode /tmp/c-uni /tmp/o-uni

echo "##### (f) space and non-ASCII source paths"; rm -rf /tmp/c-paths && cp -r /work/paths /tmp/c-paths && lockit /tmp/c-paths && cd /tmp/c-paths
cargo mutants $LF --output /tmp/o-paths 2>&1 | tail -2; echo "exit=$?"; ls /tmp/o-paths/mutants.out/diff | head -20; keep paths /tmp/c-paths /tmp/o-paths
echo "##### (g) methods, trait impls, generics"; rm -rf /tmp/c-methods && cp -r /work/methods /tmp/c-methods && lockit /tmp/c-methods && cd /tmp/c-methods
cargo mutants --list --json > /tmp/methods-list.json; python3 -c "
import json;d=json.load(open('/tmp/methods-list.json'))
for m in d: print(m['genre'], repr(m['function']['function_name']) if m.get('function') else None, m['name'])" | sort | uniq | head -30
cargo mutants $LF --output /tmp/o-methods 2>&1 | tail -2; echo "exit=$?"; keep methods /tmp/c-methods /tmp/o-methods

echo "##### (a,b,c,d,h) relative in-tree --output from a crate in a SUBDIRECTORY of a git repo"
rm -rf /tmp/repo && mkdir -p /tmp/repo/other /tmp/repo/sub/crate1 && cd /tmp/repo && echo hi > other/README
cp -r /work/subject/Cargo.toml /work/subject/src sub/crate1/ && lockit sub/crate1
$G init -q . && $G add -A && $G commit -q -m v1
cp /work/subject-v2/src/lib.rs sub/crate1/src/lib.rs && $G commit -qam v2
git diff --relative=sub/crate1 HEAD~1 HEAD > /out/subdir-relative.diff; git diff HEAD~1 HEAD > /out/subdir-toplevel.diff
echo "-- relative diff head:"; head -3 /out/subdir-relative.diff; echo "-- toplevel diff head:"; head -3 /out/subdir-toplevel.diff
cd /tmp/repo/sub/crate1
echo "-- (a,c,h) relative --output out-r with the --relative diff"
cargo mutants $LF --in-diff /out/subdir-relative.diff --output out-r 2>&1 | tail -2; echo "exit=$?"; ls out-r out-r/mutants.out 2>&1 | head -8
echo "-- (h) toplevel diff from the crate dir"
cargo mutants $LF --in-diff /out/subdir-toplevel.diff --output out-t 2>&1 | tail -3; echo "exit=$?"
echo "-- (d) NON-empty pre-created --output"; mkdir -p out-d && echo keep > out-d/keepme
cargo mutants $LF --in-diff /out/subdir-relative.diff --output out-d 2>&1 | tail -1; echo "exit=$?"; ls out-d
echo "-- (b) is the in-tree <out> copied into the mutated tree? (canary test must FAIL the baseline if copied)"
mkdir -p out-c && echo x > out-c/CANARY
cat >> src/lib.rs <<'RS'

#[cfg(test)]
mod canary {
    #[test]
    fn out_dir_is_not_in_the_copied_tree() {
        let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("out-c/CANARY");
        assert!(!p.exists(), "in-tree --output was copied into the mutant tree: {}", p.display());
    }
}
RS
cargo mutants $LF --output out-c 2>&1 | tail -3; echo "exit=$?"
python3 -c "
import json;o=json.load(open('out-c/mutants.out/outcomes.json'));b=o['outcomes'][0]
print('canary baseline:',b['summary'],[(p['phase'],p['process_status']) for p in b['phase_results']],'total_mutants',o['total_mutants'])" 2>&1
echo "-- does git see the in-tree out dir? (the post-run dirty check)"; cd /tmp/repo && git status --porcelain | head
echo "-- keep subdir case"; rm -rf /tmp/o-subdir && mkdir -p /tmp/o-subdir && cp -r /tmp/repo/sub/crate1/out-r/mutants.out /tmp/o-subdir/ && keep subdir /tmp/repo/sub/crate1 /tmp/o-subdir
git -C /tmp/repo show HEAD~1:sub/crate1/src/lib.rs > /dev/null 2>&1; cp /out/subdir-relative.diff /out/cases/subdir/in.diff; cp /out/subdir-toplevel.diff /out/cases/subdir/toplevel.diff

echo "##### (e) workspace root ABOVE the run cwd"
rm -rf /tmp/wsr && mkdir -p /tmp/wsr && cp -r /work/ws/. /tmp/wsr/ && rm -rf /tmp/wsr/target && cd /tmp/wsr && lockit . && $G init -q . && $G add -A && $G commit -q -m v1
sed -i 's/x \* 2/x * 2 + 0/' a/src/lib.rs && $G commit -qam v2
git diff --relative=a HEAD~1 HEAD > /out/ws-relative.diff; git diff HEAD~1 HEAD > /out/ws-toplevel.diff; head -3 /out/ws-relative.diff
cd /tmp/wsr/a
cargo mutants $LF --in-diff /out/ws-relative.diff --output out-w 2>&1 | tail -2; echo "exit=$?"; ls out-w 2>&1 | head -3
cargo mutants $LF --in-diff /out/ws-toplevel.diff --output out-w2 2>&1 | tail -3; echo "exit=$?"
echo DONE > /out/DONE-U0
