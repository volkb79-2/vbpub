# P109 report — exact kill and owner-safety coverage

## Result

`actions/kill_ops.py` and `actions/owner_safety.py` have exact 100% statement
and branch coverage in two complete parallel gate runs. Each run passed 2,088
cases. No product source, gate, dependency, pragma, or coverage configuration
changed.

## Evidence

Baseline:

```text
kill_ops.py lines:
48 58 98 103 135 235
kill_ops.py pairs:
47->48 54->58 97->98 101->103 133->135 234->235

owner_safety.py lines:
117 151 153 156 158 241 254 353 355
owner_safety.py pairs:
116->117 150->151 152->153 155->156 157->158 161->160
238->241 249->254 270->272
```

Both runs had empty literal intersections and:

```text
kill_ops.py missing_lines=[]
kill_ops.py missing_branches=[]
owner_safety.py missing_lines=[]
owner_safety.py missing_branches=[]
target_record_sha256=1e8d018816b6b29ff1677dbb0f6882396c48a49af71033111539318174937bee
```

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,088 passed in 57.56s | 0/0, 100% ≥ 100% | 0 |
| 2 | 2,088 passed in 63.76s | 0/0, 100% ≥ 100% | 0 |

Eighteen new cases collect as eighteen cases: 2,070 plus 18 equals 2,088.

## Behavioral coverage

Tests pin exact validation errors and preview text, all residual inspect-shape
verdicts, safe owner messages, canonical-id protection when no name exists, and
the production inspect delegation seam. Each injected inspect is called exactly
once. No test invokes Docker, systemd, a signal, or another host mutation.
