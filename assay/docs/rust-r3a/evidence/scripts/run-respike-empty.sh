#!/bin/sh
exec >/out/spikes6.log 2>&1
cd /work/subject-v2
: > /tmp/empty.diff
cargo mutants --test-tool nextest --jobs 1 --cargo-arg=--offline --in-diff /tmp/empty.diff --output /out/o-empty
echo "empty-diff real run exit=$?"
ls -la /out/o-empty 2>&1 | head -2
