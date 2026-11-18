#!/bin/sh
exec >/out/spikes4b.log 2>&1
cd /work/ws
for tw in false true; do
  echo "== no --package, --test-workspace=$tw"
  cargo mutants --test-tool nextest --jobs 1 --timeout 20 --cargo-arg=--offline --test-workspace=$tw --output /out/wsb-$tw >/dev/null 2>&1; echo exit=$?
  python3 -c "
import json;o=json.load(open('/out/wsb-$tw/mutants.out/outcomes.json'))
print({k:o[k] for k in ('total_mutants','missed','caught','timeout','unviable')})
for x in o['outcomes']:
    sc=x['scenario']
    if sc=='Baseline' or sc['Mutant']['package']=='a':
        t=[p for p in x['phase_results'] if p['phase']=='Test']
        print('Baseline' if sc=='Baseline' else sc['Mutant']['name'], x['summary'], t[0]['argv'][2:] if t else None)
" | head -4
done
