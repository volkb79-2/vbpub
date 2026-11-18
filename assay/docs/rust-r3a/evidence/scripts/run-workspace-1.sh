#!/bin/sh
exec >/out/spikes4.log 2>&1
cd /work/ws
for tw in false true; do
  echo "== --test-workspace=$tw (mutate crate a; its only test lives in crate b)"
  cargo mutants --test-tool nextest --jobs 1 --timeout 20 --cargo-arg=--offline --package a --test-workspace=$tw --output /out/ws-$tw; echo exit=$?
  python3 -c "
import json;o=json.load(open('/out/ws-$tw/mutants.out/outcomes.json'))
print({k:o[k] for k in ('total_mutants','missed','caught','timeout','unviable')})
print([x['argv'][2:] for x in o['outcomes'][0]['phase_results']][-1])"
done
echo DONE > /out/DONE4
