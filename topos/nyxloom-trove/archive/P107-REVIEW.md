# P107-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P107-network-provider-coverage
**HEAD:** 739c59d5 (confirmed)
**Verdict:** **APPROVED**

## Independent gate verification

Full xdist gate: **2062 passed, exit 0** in 53s.

```
net_netns: ml=[], mb=[], el=116, eb=34
net_bpf:   ml=[], mb=[], el=123, eb=42
PASS: both files whole-file 100%
```

Two-run parity confirmed (report). 10 functions = 10 cases, 2062 total
(2052 + 10). `git diff --check`: clean.

## Dead-branch proof (net_netns.py source edit)

Two aggregation guards were removed from `net_netns.py`. Both were proven
redundant by invariant analysis of the `collect()` method:

### Removed guard 1: child completeness check
```python
if len(child_states) != len(child_keys) or not child_states:
```
**Proof of redundancy:** The first pass iterates `for key in entities` and
places every entity into either `base` (rejection paths at lines 53,56,58,
61,63,66) or `candidates` (line 68). The second pass moves every candidate
into `base` (lines 74,81). Therefore after both passes, every entity has a
`base[key]` entry. `observations = dict(base)` copies all entries. Since
`child_keys` is built from `children[parent]` which is populated from
entities whose `.parent` matches, every child key is in `entities` and
thus in `observations`. The `len(child_states) != len(child_keys)` check
can never be true. The `not child_states` check is precluded by the
preceding `if not child_keys: continue`. **Guard was dead code.**

### Removed guard 2: namespace overlap check
```python
if sum(len(ns_ids) for ns_ids in ns_sets) != len(combined):
```
**Proof of redundancy:** In the second pass (lines 72-82), entities sharing
a namespace (`len(ns_usage[candidate.ns_id]) > 1`) receive
`contributes=False` and `ns_ids=frozenset()`. Private-namespace entities
receive `contributes=True` and `ns_ids=frozenset({candidate.ns_id})`
(single-element). During aggregation, the check at line 120 rejects any
child with `not state.contributes or not ns_ids`. Therefore all remaining
children have single-element, private ns_ids sets. Since each namespace is
unique to one entity (shared ones were filtered), all contributing ns_ids
sets are disjoint single-element sets. The sum of their cardinalities
necessarily equals the cardinality of their union. **Guard was dead code.**

### Behavioral preservation
All observable behavior is preserved: shared-namespace aggregation still
produces "aggregation proof failed" via the `contributes`/`ns_ids` check
at line 120, and missing-child aggregation cannot occur because the base
pass guarantees every entity has an observation. No reachable failure path
was removed. No pragma or omission was added.

## Test quality audit (10 tests)

| Test | Assertion type |
|------|---------------|
| Multiple namespaces rejected | Complete `NetSample` dict + complete status dict |
| Missing dev + shared namespace | Complete result dict with 3 entries + complete status dict |
| Host stat failure + status copy | `host_netns_id is None`, exact status dict, copy isolation |
| Invalid pid lines | Exact tuple `(101, 202)` |
| Missing net/dev → None | `is None` |
| Invalid BPF snapshot shape | `result == {}`, complete status dict with error |
| Invalid cgroup mappings/rows | Complete result dict with `unavailable_sample`, complete status dict |
| Snapshot read OSError | `result == {}`, complete status dict with errors, `exists.assert_called_once_with`, `read_text.assert_called_once_with` |
| Unmatched BPF entries → None | `is None` |
| Invalid cgroup ids → exact `NetSample` | Complete `NetSample` dataclass with proto dict, confidence, aggregation, unavailable_reason |

All 10 tests use complete dict/dataclass/tuple equality with causal
dependency-call receipts where applicable. Zero substring, membership,
non-None, range, len-only, or assertion-free bodies. Zero `pass`. All
patches context-managed. No function-under-test mocked.

## Scope

- Product source: `net_netns.py` — 2 dead guards removed with invariant
  proof (see above). No other source changes. ✓
- No status, CLI, gate, dependency, pragma, or omit changes. ✓
- No host namespaces, BPF maps, fixed `/tmp`, sleep, random, host-proc. ✓
