#!/bin/sh
exec >/out/spikes3.log 2>&1
cd /work/subject
F="--test-tool nextest --jobs 1 --timeout 20 --cargo-arg=--offline --in-diff /work/diffs/v2.diff --output /out/rerun"
echo "== first run"; cargo mutants $F --test-workspace=false >/dev/null 2>&1; echo exit=$?; ls -la /out/rerun; cat /out/rerun/mutants.out/outcomes.json | head -c 200 | tr -d '\n'; echo
echo "== second run into the same --output (default --test-workspace)"; cargo mutants $F 2>&1 | tail -3; echo exit=$?; ls -la /out/rerun
python3 - <<'PY'
import json,glob
for p in sorted(glob.glob('/out/rerun/mutants.out*/outcomes.json')):
    o=json.load(open(p)); b=o['outcomes'][0]
    print(p, o['start_time'], [x['argv'][-3:] for x in b['phase_results']])
PY
echo "== output dir that is an existing FILE"; : > /out/afile; cargo mutants --test-tool nextest --cargo-arg=--offline --output /out/afile --in-diff /work/diffs/v2.diff 2>&1 | tail -2; echo exit=$?
echo DONE > /out/DONE3
