#!/bin/sh
exec >/out/spikes2.log 2>&1
cd /work/subject
echo "== uni list json"; cargo mutants --list --json > /out/s2-uni-list.json; echo exit=$?
echo "== multi in-diff list (v2 tree, body-line-2 edit)"; cd /work/subject-v2
cargo mutants --list --line-col=true --in-diff /work/diffs/multi.diff; echo exit=$?
cargo mutants --list --json --in-diff /work/diffs/multi.diff > /out/s2-multi-indiff.json; echo exit=$?
echo "== zero in-diff list json"; cd /work/subject
printf '' > /tmp/empty.diff; cargo mutants --list --json --in-diff /tmp/empty.diff; echo exit=$?
echo DONE > /out/DONE2
