"""W3 Part 1 step 3: dynamic reach. Fresh interpreter per module; which assay modules load.

Usage (from assay/): python3 reach.py OUT.json   (prints a markdown summary)
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import all_modules, component  # noqa: E402

mods = all_modules()
reach = {}
for m in mods:
    code = ("import sys; sys.path.insert(0,'src'); import " + m +
            "; print(sorted(k for k in sys.modules if k.startswith('assay')))")
    out = subprocess.run(["nice", "-n", "19", "python3", "-I", "-c", code],
                         capture_output=True, text=True, check=True).stdout
    reach[m] = sorted(m2 for m2 in eval(out) if m2 in mods)  # drop non-module names
json.dump(reach, open(sys.argv[1], "w"), indent=1, sort_keys=True)

print("| imported module | modules loaded | adapter modules loaded | parser modules loaded |\n|---|---|---|---|")
for m in sorted(reach):
    r = reach[m]
    ad = [x for x in r if component(x).startswith("adapter.")]
    pa = [x for x in r if component(x).startswith("parsers.")]
    print(f"| {m} | {len(r)} | {len(ad)} | {len(pa)} |")
loaders = sorted(m for m in reach if any(component(x).startswith("adapter.") for x in reach[m])
                 and not component(m).startswith("adapter."))
print("\nnon-adapter modules whose import loads an adapter module:", loaders)
