# P107 report — exact network-provider coverage

## Result

`providers/net_netns.py` and `providers/net_bpf.py` have exact 100% statement
and branch coverage in two complete parallel gate runs. Each run passed 2,062
cases. Two redundant netns aggregation guards were removed; no gate,
dependency, pragma, or coverage configuration changed.

## Literal and whole-file evidence

Baseline:

```text
net_netns.py lines:
67 68 75 76 82 83 114 115 122 123 147 152 153 164 165 177
net_netns.py pairs:
66->67 74->75 81->82 113->114 121->122 176->177

net_bpf.py lines:
72 78 81 82 101 106 133 134 135 148 151 196
net_bpf.py pairs:
71->72 77->78 100->101 147->148 150->151 195->196
```

Both final runs produced empty literal intersections and:

| File | Missing lines | Missing branches | Executed lines | Executed branches |
| --- | --- | --- | ---: | ---: |
| `net_netns.py` | `[]` | `[]` | 116 | 34 |
| `net_bpf.py` | `[]` | `[]` | 123 | 42 |

The normalized complete target-record hash matched in both runs:

```text
d160957e5ed21f415337b338442b73c4a04a4338df063aa7936314ebbc5003ec
```

## Gate evidence

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,062 passed in 66.77s | 0/0, 100% ≥ 100% | 0 |
| 2 | 2,062 passed in 65.48s | 0/0, 100% ≥ 100% | 0 |

The ten new test functions each collect as one case: 2,052 P106 cases plus ten
P107 cases equals 2,062.

## Dead-branch proof

The removed `child_states` completeness guard was redundant: every entity is
placed in `base` during the first pass or in `candidates`, and every candidate
is placed in `base` during the second pass before `observations = dict(base)`.
The preceding `if not child_keys` also proves a processed parent has at least
one child.

The removed namespace-overlap guard was likewise redundant. Candidate
namespaces used by more than one entity are converted to non-contributing
observations before aggregation, and line 117 rejects every non-contributing
child. By induction, every retained aggregate contains only disjoint,
contributing child namespace sets. The guards had no additional observable
failure behavior.

## Behavioral coverage

Exact tests cover multiple namespaces, missing net/dev, shared namespaces,
host namespace stat failure, status copying, invalid pid lines, missing device
files, invalid snapshot shape, invalid cgroup mappings and map rows, mapped ids
without counters, snapshot read errors, unmatched entries, and invalid entry
cgroup ids.
