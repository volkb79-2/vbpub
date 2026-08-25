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

## Gate

_Pending: the registered gate is run against the final HEAD, and its real
transcript replaces this line in the follow-up commit that fills it in._
