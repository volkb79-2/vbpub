"""W3 Part 1 step 7: junit time per test component (own-partition vs full suite). Usage: durations.py JUNIT.xml"""
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coupling import expected_dir  # noqa: E402

t = defaultdict(float)
n = defaultdict(int)
for tc in ET.parse(sys.argv[1]).getroot().iter("testcase"):
    f = tc.get("classname", "").split(".")[1] + ".py" if "." in tc.get("classname", "") else ""
    d = expected_dir(f) or "root"
    t[d] += float(tc.get("time", 0))
    n[d] += 1
tot = sum(t.values())
print("| test component | testcases | seconds (junit sum) | share |\n|---|---|---|---|")
for d in sorted(t):
    print(f"| {d} | {n[d]} | {t[d]:.1f} | {100*t[d]/tot:.1f}% |")
print(f"| total | {sum(n.values())} | {tot:.1f} | 100% |")
