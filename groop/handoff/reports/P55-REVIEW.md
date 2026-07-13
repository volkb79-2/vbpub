# P55-REVIEW — Frontier review pass #2 (merge gate)

**Reviewer:** frontier review + merge-authority session (Opus high), 2026-07-13
**Verdict:** APPROVED after one pass-2 review-fix — merged `--no-ff` into `main`.

## Scope / checklist findings

`--entities GLOB` (repeatable, fnmatchcase), `--slice NAME` (validated subtree
selector), `--metrics compact` (closed enum, registry-backed
`METRIC_GROUPS`/`COMPACT_GROUPS`) added at the `Collector`/`walk_entities`
level. Collection-time entity pruning: non-matching keys skip `collect_cgroup()`
(no sysfs reads); ancestors auto-included for path completeness. Rejected with
`--replay`/`--attach` (exit 2). Wired into all three `Collector()` call sites in
`cli.py`. Filtered frames are a valid subset of the P2 schema (RecordWriter ->
RecordReader round-trip test).

- Scope clean: all 11 files under `groop/**`.
- Tests assert observable outcomes (frame entity/metric membership, ancestor
  set contents, RecordReader re-parse). No hollow tests.

### Pass-2 finding (NET-NEW — pass-1 did NOT flag it)

**Finding P55-R1 — compact mode did not drop the structured per-entity blocks.**
The handoff's `--metrics compact` drop-list covers the network / DAMON /
governance-drift blocks. In the data model these live as separate `EntityFrame`
attributes (`.network`, `.damon`, `.governance`), NOT as keys in the `metrics`
dict. The implementation pruned only `eframe.metrics`, so a compact frame still
carried the full `network` and `governance` blocks (probed: 8 network + 8
governance blocks retained per full-tree fixture) — under-delivering the
specified size reduction. The existing compact test only inspected the metrics
dict, so it passed while the blocks leaked (a subtle hollow spot).

**Fix applied** (`collector.py`, feature branch, committed by reviewer): under
compact mode also set `eframe.network/.damon/.governance = None`. Test
`test_metrics_compact_drops_network_damon_governance` strengthened to assert all
three blocks are `None` — verified meaningful because full mode populates 8
network + 8 governance blocks on the fixture.

Scope note (reviewer decision): host-level context (`frame.host`, `frame.host_meta`,
`host_damon_*`) is deliberately retained under compact as host context, akin to
ancestor auto-inclusion. The handoff's "frame.damon/frame.governance" language
maps to the per-entity blocks now pruned.

## Pass-1 (self-review) overlap — trial metric

| Pass-1 finding | flagged-by-pass-1 | pass-2 assessment |
|---|---|---|
| LOG dates wrong (2026-07-14 -> -12), fixed | yes | confirmed |
| Dead code `self._metrics_mode`, removed | yes | confirmed |
| REPORT reconstructed timing, fixed | yes | confirmed |
| Missing `--record` round-trip test, added | yes | confirmed |
| Missing `--once --json` CLI smoke, added | yes | confirmed |
| **compact leaves network/damon/governance blocks** | **no** | **pass-2 net-new (P55-R1), fixed** |

Pass-2 net-new findings: **1** (P55-R1). Pass-1 caught its five mechanical items
but missed the one behavioral contract gap — consistent with the documented
same-tier correlated-blind-spot expectation.

## Gate evidence (controller rerun, `/tmp/p52-venv`, textual 8.2.8 + zstandard)

```
$ PYTHONPATH=groop/src python -m pytest groop/tests/test_p55_filtering.py -q \
    -p no:asyncio -p no:schemathesis -W error
32 passed in 0.19s

$ PYTHONPATH=groop/src timeout 400 python -m pytest groop/tests/ -q \
    -p no:asyncio -p no:schemathesis -W error
794 passed in 68.51s (0:01:08)
```

Full suite green with `-W error` post-fix. (The REPORT's agent-env "723 passed +
11 flaky failures" was a textual-not-installed artifact; with textual present
and the schemathesis plugin disabled, the suite is fully green.) py_compile
clean on changed files.
