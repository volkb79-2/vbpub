#!/bin/sh
exec >/out/spikes5b.log 2>&1
G="git -c user.email=a@b -c user.name=x"
cd /work && rm -rf gitsub && cp -r subject gitsub && cd gitsub && git init -q . && cargo generate-lockfile --offline >/dev/null 2>&1; $G add -A && $G commit -q -m v1
cp /work/subject-v2/src/lib.rs src/lib.rs && $G commit -qam v2 && git diff HEAD~1 HEAD > /out/real-git.diff; head -6 /out/real-git.diff
echo "== design flag set on a real git diff"
cargo mutants --test-tool nextest --jobs 2 --timeout-multiplier 10 --minimum-test-timeout 120 --cargo-arg=--locked --cargo-arg=--offline --in-diff /out/real-git.diff --output /out/o-git 2>&1 | tail -3; echo exit=$?
python3 -c "
import json;o=json.load(open('/out/o-git/mutants.out/outcomes.json'));print({k:o[k] for k in ('total_mutants','missed','caught')}, o['outcomes'][0]['phase_results'][1]['argv'][1:])"
echo "== env vars cargo-mutants reads"; cargo mutants --help | grep -o "CARGO_MUTANTS_[A-Z_]*" | sort -u | tr '\n' ' '
