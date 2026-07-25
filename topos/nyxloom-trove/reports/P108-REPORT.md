# P108 report — exact host-network coverage

## Result

`providers/net_host.py` has exact 100% statement and branch coverage in two
complete parallel gate runs. Each run passed 2,070 cases. No product source,
gate, dependency, pragma, or coverage configuration changed.

## Evidence

Baseline:

```text
lines:
31 34 35 56 60 61 77 85 86 97 102 103 104 109 144 147 148
200 201 207 208 214 215 223 224 225
pairs:
30->31 55->56 76->77 96->97 108->109 143->144 146->147
199->200 206->207 213->214
```

Both runs had empty literal intersections and:

```text
missing_lines=[]
missing_branches=[]
executed_lines=147
executed_branches=42
target_record_sha256=ae4ee2ac63f9496ba38d20c5da5180c61431c50dafb81b381da69cb8747f0f3f
```

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,070 passed in 71.55s | 0/0, 100% ≥ 100% | 0 |
| 2 | 2,070 passed in 66.54s | 0/0, 100% ≥ 100% | 0 |

Eight new functions collect as eight cases: 2,062 plus eight equals 2,070.

## Behavioral coverage

Complete parser assertions cover short/non-numeric net-dev rows, short/non-hex
softnet rows, mismatched/non-numeric SNMP pairs, and blank/malformed/pre-header
qdisc lines. Complete provider assertions cover missing root, missing net/dev,
all missing auxiliary proc files, and a deterministic tc runner failure. No
host proc or tc state is consulted.
