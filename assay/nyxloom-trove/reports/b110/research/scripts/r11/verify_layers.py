#!/usr/bin/env python3
"""R11: split verify.py candidates into its three documented stages
(verify.py:1-80): raw-document cross-checks (a `document` parameter),
independent re-derivation (_check_rN_rederivation, _judge_one_attempt, and
their helpers taking a Verdict), and reconstruction glue (everything else),
and flag which raw checks name a producer twin in their docstring."""
import ast, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from candidates import sites_of
src = Path(sys.argv[1]).read_text()
tree = ast.parse(src)
def c(n): return sum(len(sites_of(x)) for x in ast.walk(n))
buckets = {"raw-cross-check": 0, "re-derivation": 0, "reconstruction/other": 0}
twins = []
for fn in tree.body:
    if not isinstance(fn, ast.FunctionDef):
        continue
    args = [a.arg for a in fn.args.args]
    doc = ast.get_docstring(fn) or ""
    n = c(fn)
    if "rederivation" in fn.name or fn.name in ("_judge_one_attempt", "_check_claim_detail"):
        buckets["re-derivation"] += n
    elif args[:1] == ["document"] or "failures" in args:
        buckets["raw-cross-check"] += n
        if "assay.verdict" in doc or "reconstruction" in doc.lower() or "Verdict." in doc:
            twins.append((fn.name, fn.lineno, n))
    else:
        buckets["reconstruction/other"] += n
print(buckets, "module-level:", c(tree) - sum(buckets.values()))
print("raw checks whose docstring names the producer twin / reconstruction:", len(twins), sum(t[2] for t in twins))
for t in twins: print("  ", t)
