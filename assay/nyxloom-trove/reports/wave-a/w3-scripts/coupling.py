"""W3 Part 1 step 5: test-component x source-component coupling from a coverage-context database.

Usage (from assay/): python3 coupling.py DB.coverage JUNIT.xml CANDIDATES.json OUT.json
Deterministic: rerunning on the same inputs reproduces OUT.json byte for byte.

Test file -> test component: expected_dir() of its basename (brief 2b); "" is reported as "root".
A candidate site is attributed to its enclosing statement start (largest statement line <= site line)
when the site's own line is not a statement line (multi-line statements).
"""
import bisect
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import coverage

sys.path.insert(0, str(Path(__file__).parent))
from common import all_modules, component  # noqa: E402

ROOT_PINNED = frozenset({
    "test_self_hosting.py", "test_runner_snapshot_selection.py", "test_lane_schema_v2_locked_successors.py",
    "test_verdict_v13_successors.py", "test_b106_reuse_and_witness.py", "test_distribution_build_release.py",
    "test_verdict_conformance.py", "test_errors.py",
    "test_python_qualification.py", "test_gate_qualify_cmru_b006a.py", "test_gate_harness_version_pins.py",
    "test_gate_qualify_dstdns_sql.py", "test_analysis.py", "test_analysis_json_framer.py",
    "test_distribution_gate.py", "test_distribution_release_wheel.py", "test_standalone.py", "test_cgroup_parent.py",
    "test_self_lane.py", "test_go_helper_is_packaged.py", "test_verdict_schema_is_packaged.py",
    "test_dependency_purity.py", "test_b105_report_check.py", "test_gate_failure_diagnostics.py"})
EXCEPTIONS = {"test_evaluate_javascript_end_to_end.py": "parsers/coverage"}
LANGS = ("python", "javascript", "go", "sql")


def expected_dir(name):
    if name in ROOT_PINNED:
        return ""
    if name in EXCEPTIONS:
        return EXCEPTIONS[name]
    words = name[len("test_"):-len(".py")].split("_")
    if words[0] == "adapters" and words[1] in LANGS:
        return f"adapters/{words[1]}"
    if words[0] == "coverage":
        return "parsers/coverage"
    if name in ("test_mutation_format_registry.py", "test_mutation_report_json_parser.py"):
        return "parsers/mutation"
    if words[:2] in (["result", "report"], ["result", "reports"]):
        return "parsers/result_reports"
    for lang in LANGS:
        if lang in words:
            return f"adapters/{lang}"
    return "core"


OWN = {  # source component -> test components counted as "own partition"
    "core": {"core"}, "cli": {"core"}, "analysis": {"root"},
    "adapter.python": {"adapters/python"}, "adapter.javascript": {"adapters/javascript"},
    "adapter.go": {"adapters/go"}, "adapter.sql": {"adapters/sql"},
    "parsers.coverage": {"parsers/coverage"}, "parsers.mutation": {"parsers/mutation"},
    "parsers.result_reports": {"parsers/result_reports"},
}


def tcomp(nodeid):
    fname = nodeid.split("::")[0].rsplit("/", 1)[-1]
    return expected_dir(fname) or "root"


def main():
    db, junit, candfile, out = sys.argv[1:5]
    cand = json.load(open(candfile))
    cwd = Path.cwd()
    mods = all_modules()
    file_comp = {str(p): component(m) for m, p in mods.items()}
    data = coverage.CoverageData(basename=db)
    data.read()
    cov = coverage.Coverage(data_file=db, config_file=False)
    cov.load()
    raw_ctx = set(data.measured_contexts())
    norm = lambda c: re.sub(r"\|(setup|run|teardown)$", "", c)  # noqa: E731
    nodeids = sorted({norm(c) for c in raw_ctx if c})
    testcases = len(list(ET.parse(junit).getroot().iter("testcase")))
    stats = {"raw_contexts": len(raw_ctx), "distinct_nodeids": len(nodeids), "junit_testcases": testcases,
             "core": "ctrace-expected", "has_nonempty_context": bool(nodeids)}
    if not nodeids:
        print("no non-empty context: STOP (sysmon/wrong-core symptom)")
        sys.exit(3)

    matrix = defaultdict(lambda: defaultdict(set))       # test comp -> src comp -> {(file,line)}
    line_tcomps = defaultdict(set)                       # (file,line) -> test comps ("" key = import-time)
    union_lines = defaultdict(set)                       # src comp -> lines
    empty_ctx_lines = 0
    measured = {}
    for f in data.measured_files():
        rel = str(Path(f).resolve().relative_to(cwd)) if Path(f).is_absolute() else f
        if rel not in file_comp:
            continue
        sc = file_comp[rel]
        cbl = data.contexts_by_lineno(f)
        measured[rel] = set(data.lines(f) or [])
        for line, ctxs in cbl.items():
            union_lines[sc].add((rel, line))
            for c in ctxs:
                if c == "":
                    empty_ctx_lines += 1
                    line_tcomps[(rel, line)].add("")
                else:
                    tc = tcomp(norm(c))
                    matrix[tc][sc].add((rel, line))
                    line_tcomps[(rel, line)].add(tc)
    union_check = {}
    for sc in sorted(set(file_comp.values())):
        expect = sum(len(measured[r]) for r, c in file_comp.items() if c == sc and r in measured)
        got = len(union_lines[sc])
        union_check[sc] = {"union_over_contexts": got, "coverage_lines": expect, "equal": got == expect}
    stats["empty_context_line_entries"] = empty_ctx_lines

    # candidate sites
    stmts = {}
    for rel in file_comp:
        try:
            stmts[rel] = sorted(cov.analysis2(str(cwd / rel))[1])
        except coverage.CoverageException:
            stmts[rel] = []
    classes = defaultdict(lambda: defaultdict(int))
    remapped = 0
    for s in cand["sites"]:
        rel, line = s["file"], s["line"]
        sc = file_comp[rel]
        st = stmts[rel]
        if line not in set(st):
            i = bisect.bisect_right(st, line) - 1
            if i >= 0:
                line = st[i]
                remapped += 1
        tcs = line_tcomps.get((rel, line), set())
        real = {t for t in tcs if t != ""}
        own = OWN[sc]
        if not tcs:
            k = "none"
        elif not real:
            k = "import_time_only"
        elif real <= own:
            k = "own_only"
        elif real & own:
            k = "both"
        else:
            k = "foreign_only"
        classes[sc][k] += 1
    stats["sites_remapped_to_statement_start"] = remapped

    result = {
        "stats": stats,
        "matrix_distinct_lines": {t: {s: len(v) for s, v in sorted(row.items())} for t, row in sorted(matrix.items())},
        "union_check": union_check,
        "candidate_classes": {sc: dict(sorted(v.items())) for sc, v in sorted(classes.items())},
        "own_partition": {k: sorted(v) for k, v in sorted(OWN.items())},
    }
    Path(out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
