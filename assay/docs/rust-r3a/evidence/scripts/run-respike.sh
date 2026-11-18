#!/bin/sh
exec >/out/spikes5.log 2>&1
F="--test-tool nextest --cargo-arg=--offline"
echo "== help: any env var for --in-diff?"; cargo mutants --help | grep -i -E "env|CARGO_MUTANTS" | head; cargo mutants --help | grep -n -i -B1 -A3 "in-diff" | head -12
echo "== 1 comment-only diff, real run: does --output dir exist afterwards?"
cd /work/subject-cm; cargo mutants $F --jobs 1 --in-diff /work/diffs/comment.diff --output /out/o-zero >/dev/null 2>&1; echo exit=$?; ls -la /out/o-zero 2>&1 | head -3
echo "== 2 empty diff, real run"
: > /tmp/empty.diff; cargo mutants $F --jobs 1 --in-diff /tmp/empty.diff --output /out/o-empty 2>&1 | tail -2; echo "exit=${PIPESTATUS:-?}"; ls -la /out/o-empty 2>&1 | head -3
echo "== 3 diff mismatch, real run: output dir after exit 5?"
cd /work/subject; cargo mutants $F --jobs 1 --in-diff /work/diffs/v2.diff --output /out/o-mismatch >/dev/null 2>&1; echo exit=$?; ls -la /out/o-mismatch 2>&1 | head -3
echo "== 4 pre-created EMPTY mutants.out inside --output: rotated or reused?"
mkdir -p /out/o-pre/mutants.out; cd /work/subject-v2
cargo mutants $F --jobs 1 --timeout 20 --in-diff /work/diffs/v2.diff --output /out/o-pre >/dev/null 2>&1; echo exit=$?; ls -la /out/o-pre; ls /out/o-pre/mutants.out | head -3
echo "== 5 pre-created empty --output dir only"
mkdir -p /out/o-pre2; cargo mutants $F --jobs 1 --timeout 20 --in-diff /work/diffs/v2.diff --output /out/o-pre2 >/dev/null 2>&1; echo exit=$?; ls /out/o-pre2
echo "== 6 workspace, flag UNSET vs =true vs =false (test argv of a mutant of a)"
cd /work/ws
for tw in unset true false; do
  if [ $tw = unset ]; then A=""; else A="--test-workspace=$tw"; fi
  cargo mutants $F --jobs 1 --timeout 20 $A --output /out/w-$tw >/dev/null 2>&1; echo "tw=$tw exit=$?"
  python3 -c "
import json;o=json.load(open('/out/w-$tw/mutants.out/outcomes.json'))
for x in o['outcomes']:
    sc=x['scenario']
    if sc!='Baseline' and sc['Mutant']['package']=='a':
        t=[p for p in x['phase_results'] if p['phase']=='Test'][0]; print(x['summary'], t['process_status'], t['argv'][2:]); break"
done
echo "== 7 real git diff format (index line, a/ b/ prefixes) and the design's flags"
cd /work && rm -rf gitsub && cp -r subject-v2 gitsub && cd gitsub && git init -q . && git -c user.email=a@b -c user.name=x add -A && git -c user.email=a@b -c user.name=x commit -q -m v2 && cp /work/subject/src/lib.rs src/lib.rs && git -c user.email=a@b -c user.name=x commit -qam v1 && cp /work/subject-v2/src/lib.rs src/lib.rs && git diff HEAD > /out/real-git.diff; head -5 /out/real-git.diff
git -c user.email=a@b -c user.name=x commit -qam v2b
cargo mutants $F --jobs 2 --timeout-multiplier 10 --minimum-test-timeout 120 --cargo-arg=--locked --in-diff /out/real-git.diff --output /out/o-git 2>&1 | tail -2; echo exit=$?
echo "== 8 --in-diff via env CARGO_MUTANTS_IN_DIFF?"
cd /work/subject-v2; CARGO_MUTANTS_IN_DIFF=/work/diffs/v2.diff cargo mutants --list 2>&1 | wc -l
echo DONE > /out/DONE5
