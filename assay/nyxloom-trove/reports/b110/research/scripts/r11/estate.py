#!/usr/bin/env python3
"""R11 estate scan: helper categories and cross-project clones.

For each project source root, classify every function by the helper
categories below (feature-based, AST), and count functions / lines / assay-R2
candidates (same catalogue as candidates.py). Then list function NAMES that
recur in >= 2 projects among helper-sized functions, and cross-project T2
function clones (dupes.Normalizer).

Usage: estate.py <clone_root> <out.json>
"""
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from candidates import sites_of  # noqa: E402
from dupes import norm_hash, size  # noqa: E402

ROOTS = {
    "assay": "assay/src/assay",
    "ciu": "ciu/src/ciu",
    "cmru": "cmru/src/cmru",
    "nyxloom": "nyxloom/src/nyxloom",
    "run-gate": "run-gate-project/run-gate.py",
    "topos": "topos/src/topos",
    "pwmcp": "pwmcp/scripts",
    "lib-worktree": "libraries/worktree/src/worktree",
    "lib-cli-extended": "libraries/cli-extended/src/cli_extended",
}

HELPER_MAX_LINES = 80
DUR_RE = re.compile(r"\[smhd\]|\(\?:?ms\||\[0-9\]\+\)\(\[smhd|[0-9]\+\)\s*\(s\|m\|h|\bs\|m\|h\|d\b")


def ncand(node):
    return sum(len(sites_of(n)) for n in ast.walk(node))


def features(fn):
    f = set()
    src_consts = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            func = n.func
            name = ast.unparse(func)
            if name.startswith("subprocess.") or name in ("run", "Popen", "check_output"):
                f.add("subprocess")
            if name in ("tomllib.load", "tomllib.loads", "tomli.load", "tomli.loads", "toml.load", "toml.loads"):
                f.add("toml-load")
            if name.startswith("hashlib.") or name.endswith(".hexdigest"):
                f.add("hashing")
            if name in ("os.replace", "os.rename") :
                f.add("_replace")
            if name in ("os.fsync",):
                f.add("_fsync")
            if name.endswith("NamedTemporaryFile") or name.endswith("mkstemp"):
                f.add("_tmp")
            if name == "json.dumps" and any(k.arg == "sort_keys" for k in n.keywords):
                f.add("canonical-json")
            if name in ("json.load", "json.loads"):
                f.add("json-load")
            if name.endswith("is_relative_to") or name.endswith("commonpath"):
                f.add("path-containment")
            if name == "os.open" and "O_NOFOLLOW" in ast.unparse(n):
                f.add("nofollow-io")
            if name.endswith(".resolve"):
                f.add("_resolve")
        if isinstance(n, (ast.List, ast.Tuple)) and n.elts and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == "git":
            f.add("git-argv")
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            src_consts.append(n.value)
    if "_replace" in f and ("_tmp" in f or "_fsync" in f or any(".tmp" in s for s in src_consts)):
        f.add("atomic-write")
    if any(DUR_RE.search(s) for s in src_consts) or re.search(r"duration|parse_(seconds|timeout|budget)", fn.name):
        f.add("duration-parse")
    if "git-argv" in f and ("subprocess" in f):
        f.add("git-wrapper")
    return {x for x in f if not x.startswith("_")}


def iter_files(root):
    p = Path(root)
    if p.is_file():
        yield p
        return
    for q in sorted(p.rglob("*.py")):
        parts = set(q.parts)
        if q.is_symlink() or "__pycache__" in parts or "tests" in parts or "fixtures" in parts or q.name.startswith("test_") or ".venv" in parts:
            continue
        yield q


def main():
    base = Path(sys.argv[1])
    out = sys.argv[2]
    table = {}
    names = defaultdict(list)
    hashes = defaultdict(list)
    for proj, rel in ROOTS.items():
        cat = defaultdict(lambda: {"funcs": 0, "lines": 0, "cand": 0, "helper_funcs": 0, "helper_lines": 0, "helper_cand": 0, "where": []})
        tot = {"files": 0, "lines": 0, "cand": 0}
        for f in iter_files(base / rel):
            try:
                text = f.read_text(encoding="utf-8")
                tree = ast.parse(text)
            except (SyntaxError, UnicodeDecodeError):
                continue
            tot["files"] += 1
            tot["lines"] += text.count("\n")
            tot["cand"] += ncand(tree)
            frel = str(f.relative_to(base))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                lines = fn.end_lineno - fn.lineno + 1
                c = ncand(fn)
                feats = features(fn)
                for k in feats:
                    d = cat[k]
                    d["funcs"] += 1; d["lines"] += lines; d["cand"] += c
                    if lines <= HELPER_MAX_LINES:
                        d["helper_funcs"] += 1; d["helper_lines"] += lines; d["helper_cand"] += c
                        d["where"].append(f"{frel}:{fn.lineno} {fn.name}")
                if lines <= HELPER_MAX_LINES and feats:
                    names[fn.name.lstrip("_")].append((proj, f"{frel}:{fn.lineno}", lines, c, sorted(feats)))
                if size(fn) >= 25:
                    hashes[norm_hash(fn)].append((proj, f"{frel}:{fn.lineno}", fn.name, lines, c))
        table[proj] = {"total": tot, "categories": {k: dict(v) for k, v in cat.items()}}
    # print summary
    cats = sorted({k for p in table.values() for k in p["categories"]})
    print("project totals:")
    for proj, d in table.items():
        print(f"  {proj:18s} files={d['total']['files']:4d} lines={d['total']['lines']:6d} cand={d['total']['cand']:5d}")
    print("\nhelper-sized (<=%d lines) functions per category: funcs/lines/cand" % HELPER_MAX_LINES)
    print("  " + "category".ljust(18) + "".join(p[:10].rjust(16) for p in table))
    for k in cats:
        row = "  " + k.ljust(18)
        for proj in table:
            d = table[proj]["categories"].get(k)
            row += (f"{d['helper_funcs']}/{d['helper_lines']}/{d['helper_cand']}" if d else "-").rjust(16)
        print(row)
    shared_names = {n: v for n, v in names.items() if len({p for p, *_ in v}) >= 2}
    print("\nhelper names recurring across >=2 projects:")
    for n, v in sorted(shared_names.items(), key=lambda kv: -len(kv[1])):
        print(f"  {n}: " + "; ".join(f"{p} {w} ({l}L,{c}c)" for p, w, l, c, _ in v[:6]))
    cross = [v for v in hashes.values() if len({p for p, *_ in v}) >= 2]
    print(f"\ncross-project T2 function clones: {len(cross)} clusters")
    for v in cross:
        print("  " + "; ".join(f"{p} {w} {n} ({l}L,{c}c)" for p, w, n, l, c in v[:6]))
    json.dump({"table": table, "shared_names": shared_names, "cross_clones": cross}, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
