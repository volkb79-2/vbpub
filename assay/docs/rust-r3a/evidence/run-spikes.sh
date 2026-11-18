#!/bin/sh
# Runs inside tester-unified-rust:local, --network none. /work = spike tree, /out = results.
cd /work || exit 99
exec >/out/spikes.log 2>&1
set -u
echo "cargo-mutants: $(cargo mutants --version)"; rustc --version; cargo nextest --version | head -1
run() { name=$1; shift; echo "=== $name: $*"; "$@"; echo "exit=$?"; }
FLAGS="--test-tool nextest --test-workspace=false --jobs 1 --timeout 20 --cargo-arg=--offline"

# S3a: list with spans, JSON
cd /work/subject
run s3-list-json sh -c 'cargo mutants --list --json > /out/s3-list.json; echo bytes=$(wc -c </out/s3-list.json)'
run s3-list-text sh -c 'cargo mutants --list --line-col'

# S3b: zero-mutant --in-diff (comment-only diff, applied to the comment tree)
cd /work/subject-cm
run s3-indiff-zero cargo mutants $FLAGS --in-diff /work/diffs/comment.diff --output /out/zero
ls -la /out/zero/mutants.out 2>&1

# S3c: --in-diff whose new side does not match the tree (v2 diff on the v1 tree)
cd /work/subject
run s3-indiff-mismatch cargo mutants $FLAGS --in-diff /work/diffs/v2.diff --output /out/mismatch

# S3d: --in-diff matching (v2 diff on v2 tree)
cd /work/subject-v2
run s3-indiff-match cargo mutants $FLAGS --in-diff /work/diffs/v2.diff --output /out/indiff

# S3e: full run on the v1 subject (caught, missed, unviable, timeout)
cd /work/subject
run s3-full cargo mutants $FLAGS --output /out/full

# S3f: --test-workspace default vs false is irrelevant on 1 crate; record flag acceptance only
run s3-testworkspace-true cargo mutants --list --test-workspace=true

# S4: baseline failure report
cd /work/subject-bf
run s4-baseline-fail cargo mutants $FLAGS --output /out/basefail
ls -la /out/basefail/mutants.out 2>&1

# Reservation/dirty-tree facts: files cargo-mutants leaves in the source tree
cd /work/subject
run s3-tree-after sh -c 'ls -la /work/subject; ls -la /work/subject/mutants.out 2>&1 | head'
echo DONE > /out/DONE
