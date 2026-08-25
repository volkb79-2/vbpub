# assay B030/B031/B032 — observability remediation — LOG

**Branch:** `fix/assay-b030-b032-observability-remediation`
**Base:** `main` at `142143a4`
**Filings:** B030, B031, B032 in `nyxloom-trove/4-backlog.md`, from
`reports/assay-review-gap-audit-2026-08-25.md` §6 (`8a2a4731`, assay-v2.2.0).
**Evidence:** `reports/assay-B030-B032-remediation-REPORT.md` (per-item
before/after transcripts driven through the CLI, plus the gate transcript).

One entry per commit, newest last. Each entry carries a sha256 over its own
commit hash + body so a later edit to this file is detectable:

```sh
python -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
```

## Commits

### `6a0f9a04`

B030 (A-319): `assay plan` discovers candidates against the consumer's own project root. `_cmd_plan`'s `_relocate_source_roots` call -- which respelled `judge.source_root_paths` against `prepared.spec.scratch_root / "unused"`, a directory that is never created, while the target resolvers were handed the REAL `prepared.spec.repo_top` -- is deleted outright. Reported `candidate_count` goes 0 -> 1 on a real lane, matching a real run's own count and candidate id; a `mode = "whole_target"` lane plans instead of failing on a phantom path. The frozen test assertion (`== 0` against a one-candidate fixture) is corrected, and a second test covers the whole-target half. `docs/CONSUMERS.md`'s plan description is verified true and its runtime estimate described honestly as a declaration-derived upper bound.

**entry-sha256:** `1d41020db68b600460657c5cd3861e88d92e28709d4b0d89ef7dded30bbf9672`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

### `ae09425d`

B031 (A-320, A-323) + B032 (A-321 scope, A-322 fix). B031: the R2 progress NDJSON is no longer written unconditionally into the consumer's live worktree -- it is opt-in and consumer-directed via `assay run --progress PATH`, absent by default, which closes the reproduced `NO_MEASUREMENT`/`DIRTY_TREE`-on-the-second-run defect and the lane-name path-traversal gap by construction. `mutation.progress_artifact` is REMOVED from the dataclass, the wire payload, the JSON Schema and the W2 frozen lock together (never populated; its only legal grammar could name nothing but the location A-292 forbids). `mutation.candidate_ids` is registered in `verify.py` and given its first real producer on the `--shard` path. A-323: `judgment.r2.shard_index`/`shard_count` had the identical, previously unfiled `verify.py` gap -- found by round-tripping a real `assay run --shard 0/1` artifact back through `assay verify`, which rejected assay's own output -- and are registered too. The progress stream gains a `run` header (commit + start time) and renames its whole-file digest `mutated_file_sha256` so it stops colliding with the verdict's replacement-text `replacement_sha256`. B032: a probe that exhausts its cap now reports `BUDGET_EXCEEDED`/`LANE_TIMEOUT` exit 4 instead of `ERROR`/`BAD_LANE_CONFIG` exit 2, while every other probe failure keeps `BAD_LANE_CONFIG`; the 30 s cap (`PROBE_BUDGET_SECONDS`) is applied as `execute_plan`'s `timeout=` argument, the value it actually reads (a `sleep 45` probe under a 5m budget went from PASS-after-46s to BUDGET_EXCEEDED-after-30s); and a probe refusal writes B010's asked-for clear message -- lane, cause, declared wrapper -- to a caller-supplied `diagnostics` stream (was 0 bytes).

**entry-sha256:** `b9c71bc188de440a73978cdc50659a68be35ef0ff0b84eecb22dd4f3954410d5`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)
### `d58265bc`

The A-320 `candidate_ids` producer is guarded against an empty shard: `Mutation.__post_init__` refuses `candidate_ids=()` outright ('must be omitted when empty'), so a shard index that legitimately draws no candidate (4 shards over 2 candidates) would have turned an honest empty shard into a crash. The field stays ABSENT in that case, which is what the schema's own 'omitted, never empty' wording already required.

**entry-sha256:** `523ac8b96a3d9d9028a1693e33a279077ac3a365e9ae80c91da978f7fdc42f60`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

### `0e6cab39`

This LOG and `assay-B030-B032-remediation-REPORT.md` themselves. Documentation only; no source, test, schema or frozen-asset change. Its own gate transcript is the one recorded below, run against this commit.

**entry-sha256:** `963ab4383a7a5d01c0c17e9717b4ccbbe9d6caa0d38f2b05666957429bec1804`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

## Gate

The REAL registered gate, run from the assay project root against the final
HEAD (`0e6cab39`), with its exit code captured separately from the run and read
in a separate step:

```
$ ( bash tools/tester-unified-gate.sh .. > /tmp/gate3.log 2>&1; echo $? > /tmp/gate3.exit )
$ cat /tmp/gate3.exit
0
$ grep -n 'ASSAY_GATE_PHASE\|ASSAY_REGISTERED_GATE_COMPLETE\|QUALIFIED' /tmp/gate3.log
22:ASSAY_GATE_PHASE=wheel-installed
25:ASSAY_GATE_PHASE=attestation-hardened
28:ASSAY_GATE_PHASE=verdict-v5-accepted
31:ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
33:ASSAY_GATE_PHASE=verdict-v6-successors-verified
36:ASSAY_GATE_PHASE=verdict-v7-successors-verified
40:ASSAY_GATE_PHASE=self-hosted-lane-passed
41:ASSAY_GATE_PHASE=topos-qualified
55:ASSAY_B006A_CMRU_QUALIFIED=1
56:ASSAY_GATE_PHASE=cmru-b006a-qualified
59:ASSAY_GATE_PHASE=independent-self-hosting-passed
60:ASSAY_REGISTERED_GATE_COMPLETE=1
```

`verdict-v7-successors-verified` is the phase that exercises
`nyxloom-trove/carve-assets/W2/test_acceptance_v7.py`, including
`test_shipped_schema_is_byte_identical_to_the_locked_v7_asset` -- the drift
guard that lives OUTSIDE `tests/` and that A-316 records as having caught real
drift only at release time. `carve-assets/W2/verdict.schema.v7.json` was
re-witnessed by copying the shipped schema over it and diffing before
committing; the diff is exactly the five removed `progress_artifact` lines and
nothing else, which independently confirms the lock was otherwise in sync.

`self-hosted-lane-passed` is assay running its own R2 lane over its own source
tree (`tester-unified: PASS (exit 0)` at commit `0e6cab39`);
`topos-qualified` / `cmru-b006a-qualified` drive REAL `assay run` invocations
against pinned disposable Topos and CMRU trees and compare complete artifacts
against frozen templates -- the path A-317/A-318 record as the only one that
answers byte-for-byte real-run fidelity rather than schema validity alone.

Also run, separately, against the same tree:

```
$ PYTHONPATH=$PWD/src python -m pytest tests/ -q -p no:randomly
3271 passed, 11 skipped
```

(necessary but NOT sufficient on its own -- it never reaches
`nyxloom-trove/carve-assets/`, which is where B010/B012's original defects hid.)

