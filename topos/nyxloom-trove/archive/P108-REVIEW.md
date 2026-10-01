# P108-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P108-host-network-provider-coverage
**HEAD:** bcc8734f (confirmed)
**Verdict:** **APPROVED**

## Independent gate verification

Full xdist gate: **2070 passed, exit 0** in 66s.

```
net_host.py: ml=[], mb=[], el=147, eb=42
PASS: whole-file 100%
```

Two-run parity confirmed (report). 8 functions = 8 cases, 2070 total
(2062 + 8). `git diff --check`: clean. No product source, gate,
dependency, pragma, or omit changes.

## Literal line/arc verification

All 26 lines and 10 branch pairs closed. Verified against source at HEAD.

| Category | Lines/arcs |
|----------|-----------|
| net-dev parser | 31, 34, 35 + arc 30→31 |
| softnet parser | 56, 60, 61 + arc 55→56 |
| SNMP parser | 77, 85, 86 + arc 76→77 |
| qdisc parser | 97, 102, 103, 104, 109 + arc 96→97, 108→109 |
| collect (without root / missing net-dev) | 144, 147, 148 + arc 143→144, 146→147 |
| missing auxiliary files | 200, 201, 207, 208, 214, 215 + arc 199→200, 206→207, 213→214 |
| tc runner failure | 223, 224, 225 |

The controller corrected these three category labels after review. The
reviewer's literal sets, coverage verdict, and test audit were unchanged.

## Test quality audit (8 tests, all exact structural equality)

| Test | Assertion |
|------|-----------|
| `parse_net_dev` short/non-numeric rows | `== {}` (nothing parsed) |
| `parse_softnet` short/non-hex rows | Exact dict: `cpu_count=1, dropped=2, time_squeeze=3` |
| `parse_snmp_like` mismatch/non-numeric | Exact dict: `{"Tcp": {"Good": 7}}` |
| `parse_qdisc` blank/malformed/pre-header | Exact dict: `{"eth0": {"dropped":2, "overlimits":3, "backlog_bytes":4, "backlog_packets":5}}` |
| Collect without root entity | `result == {}`, exact status dict |
| Collect without net/dev | Exact `unavailable_sample` dict with `source_label`/`confidence`, exact status dict with error |
| Collect with missing aux files | Complete `NetSample` dataclass with `proto` dict (tcp/udp with all-None fields), complete status dict with 3 errors, interfaces, protocols, qdisc |
| Qdisc runner failure | `_read_qdisc() is None`, exact status dict with `"tc:FileNotFoundError"` |

All 8 tests use complete dict/dataclass/SetSample equality. Zero substring,
membership, non-None, range, len-only, or assertion-free bodies. All
filesystem tests use `tmp_path`. Time patched only for status stabilization.
No function-under-test mocked. No host `/proc` or `tc` consulted.
