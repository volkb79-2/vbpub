#!/usr/bin/env python3
"""R11: per project, the UNION of helper-sized (<=80 line) functions that
carry any infrastructure feature (estate.py categories), so a function in two
categories counts once. Upper bound on what an estate-core could absorb."""
import json, sys, ast
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from candidates import sites_of
base = Path(sys.argv[1]); d = json.load(open(sys.argv[2]))
cats_infra = ["git-wrapper","subprocess","atomic-write","toml-load","json-load","hashing","duration-parse","canonical-json","path-containment","nofollow-io"]
cache = {}
def fn_at(rel, line):
    if rel not in cache:
        cache[rel] = {n.lineno: n for n in ast.walk(ast.parse((base/rel).read_text())) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    return cache[rel][line]
tot = [0,0,0]
for proj, t in d["table"].items():
    seen = {}
    for k in cats_infra:
        for w in t["categories"].get(k, {}).get("where", []):
            loc = w.split(" ")[0]; rel, line = loc.rsplit(":",1)
            seen[loc] = fn_at(rel, int(line))
    lines = sum(n.end_lineno-n.lineno+1 for n in seen.values())
    cand = sum(len(sites_of(x)) for n in seen.values() for x in ast.walk(n))
    print(f"{proj:18s} infra helpers={len(seen):4d} lines={lines:5d} cand={cand:4d}  (project total cand {t['total']['cand']})")
    if proj != "assay":
        tot[0]+=len(seen); tot[1]+=lines; tot[2]+=cand
print("non-assay estate total", tot)
