---
kind: backlog-entry
schema_version: 1
id: CP-37
title: "cgroup file readers accept Python-only number spellings (1_000, +5) and non-finite values outside read_pressure; pin the readers to the kernel's decimal grammar"
status: open
type: "bugfix"
severity: "low"
component: "metrics"
context_estimate: "small"
provenance: "Pre-release deep review of the 1.1.0 wave branch, 2026-10-27 (fable-cgprofile-20261027), hostile-file probe of lib/util.py"
filed_by: "Claude (cgprofile 1.1.0 pre-release review)"
spec_owner: "cgroup-profiler sampler"
priority: 3
filed_date: "2026-10-27"
---

## Observed (probe, 2026-10-27)

`util.read_int`, `read_kv` and `read_flat_keyed` parse with Python `int()`, which accepts `1_000` (-> 1000), `+5`,
surrounding whitespace and Unicode digits; `read_flat_keyed` lets a repeated key on one line win last
(`rbytes=1 rbytes=2` -> 2) and stores a device line with no fields as `{}`. The kernel never emits any of these, so
the practical effect is nil for a healthy host, and 1.1.0 already closes the one case with a downstream consequence
(`read_pressure` dropping `nan`/`inf`, which `json.dumps` would have written as non-JSON tokens). `parse_size`
similarly accepts `1.5GiB` with a locale-free `float()`, which is intended.

## Required contract

- One shared `_kernel_int(text) -> Optional[int]` (ASCII `[0-9]+`, optional leading `-` only where the kernel can
  write one) used by the three readers; anything else is `None`/skipped as today.
- `read_flat_keyed`: a repeated key within one line is a parse error for that line (skip the line), not last-wins.
- Keep "absent is `None`, never 0" and the existing tests byte-identical; add one hostile-content test per reader.

## Oracles

`read_int` of `1_000\n` is `None`; `read_kv` of `high +5` has no `high`; a duplicated key line in `io.stat` is
dropped while the other device lines are kept.
