"""W3 Part 1 steps 1-2: component map, candidates per component, static graph, cycles.

Usage (from assay/): python3 nyxloom-trove/reports/wave-a/w3-scripts/graph.py
Needs candidates.json from scripts/r11/candidates.py (path in argv[1]).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import all_modules, component, import_edges  # noqa: E402

mods = all_modules()
cand = json.load(open(sys.argv[1]))
print(f"modules: {len(mods)}")
comp = defaultdict(list)
for m, p in mods.items():
    comp[component(m)].append(m)
print("\n## 1 Map\n\n| component | modules | lines | candidates |\n|---|---|---|---|")
tl = tc = 0
for c in sorted(comp):
    lines = sum(p.read_text(encoding="utf-8").count("\n") for m, p in mods.items() if component(m) == c)
    n = sum(cand["per_file"][str(p)]["n"] for m, p in mods.items() if component(m) == c)
    tl += lines
    tc += n
    print(f"| {c} | {len(comp[c])} | {lines} | {n} |")
print(f"| total | {len(mods)} | {tl} | {tc} |")
print("\nmembers:")
for c in sorted(comp):
    print(f"- {c}: " + ", ".join(m.removeprefix('assay.') or 'assay' for m in comp[c]))

edges = import_edges(mods)
print(f"\n## 2 Graph\n\nmodule edges (assay-internal): {len(edges)}")
tc_only = sorted(e for e, k in edges.items() if k == {"type_checking"})
print(f"type_checking-only edges: {tc_only}")
agg = defaultdict(set)
for (a, b), k in edges.items():
    if component(a) != component(b):
        agg[(component(a), component(b))].add((a, b, "/".join(sorted(k))))
print("\ncomponent edges (importer -> imported: module-edge count)\n")
for (a, b) in sorted(agg):
    print(f"- {a} -> {b}: {len(agg[(a, b)])}")
    for x, y, k in sorted(agg[(a, b)]):
        print(f"    - {x} -> {y} [{k}]")


def cycles(graph):
    found = set()
    for start in graph:
        stack = [(start, [start])]
        while stack:
            n, path = stack.pop()
            for nx in graph.get(n, ()):
                if nx == start and len(path) > 1:
                    i = path.index(min(path))
                    found.add(tuple(path[i:] + path[:i]))
                elif nx not in path:
                    stack.append((nx, path + [nx]))
    return sorted(found)


cg = defaultdict(set)
for (a, b) in agg:
    cg[a].add(b)
print("\ncomponent cycles:", cycles(cg) or "none")
mg = defaultdict(set)
for (a, b) in edges:
    mg[a].add(b)
mc = cycles(mg)
print(f"module cycles ({len(mc)}):")
for c in mc:
    print("  ", " -> ".join(c))
print("\nimplicit edge: every 'assay.X' import first executes assay/__init__.py, which imports:",
      sorted(b for (a, b) in edges if a == "assay"))
