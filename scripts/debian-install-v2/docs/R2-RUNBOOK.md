# R2 runbook: three shards, one merge, resume

`run-gate.toml` `[lanes.r2]` is a NATIVE Assay lane (`kind = "assay"`), so
Run-Gate owns the flexible-campaign flags (RG-265) and shards the run. The judge
is the judged worktree's own `assay/` source (RG-285, no zipapp); candidates are
the diff's added lines under `source_roots`, base from the request
(`base_source = "request"`); `10m` per candidate is explicit, so no verdict
moves with host load (DIV2-BL-22). Policy: `assay.toml [lanes.r2]`.

## 1. Plan before you run (never guess the shard split)

```bash
cd scripts/debian-install-v2 && PYTHONPATH=$PWD/../../assay/src \
  /opt/mdt/venv/bin/python3 -m assay.cli plan r2 --file assay.toml \
  --request-base fdf60ffdc
```

Measured 2026-11-26 on base `fdf60ffdc`, head `f465f0b73`: whole **194** / 320
`max_mutants`; shard `0/3` **58**, `1/3` **72**, `2/3` **64** (sum 194). Shards are
hash-based (`blake2b(candidate_id, 4) % N`), not per file, and `assay plan`
refuses an uncommitted `assay.toml`: commit first.

## 2. Allocation, and one commit for everything

This `2g` lane takes `ceil(2048/1280) = 2` of a host's 4 admission slots, so the
three shards need two hosts. Use ONE commit throughout -- a store carries the plan
it was produced for, so a merge over another commit either refuses it
(`changed-plan`, `foreign-judge`) or binds a verdict to a tree it was not.

## 3. Run the three shards (the controller does this)

```bash
RG="scripts/controller/remote-gate.sh"; SP=scripts/debian-install-v2
$RG r1002 $SP r2 <sha> --base fdf60ffdc \
  --collect-store .run-gate/assay-shards/$SP -- --shard 0/3   # 58, r1002
$RG v1001 $SP r2 <sha> --base fdf60ffdc \
  --collect-store .run-gate/assay-shards/$SP -- --shard 1/3   # 72, v1001
$RG r1002 $SP r2 <sha> --base fdf60ffdc \
  --collect-store .run-gate/assay-shards/$SP -- --shard 2/3   # 64, after 0/3
```

`--base fdf60ffdc` is mandatory remotely: with no upstream and no CIU fork-point
record, `base_source = "request"` makes the lane refuse (`NOT_RUN`/`no-base`) instead
of guessing. A host without a Cowtree btrfs cheap-clone workspace refuses
`--jobs 2` by name; add `--jobs 1` there. Each shard is its own instance
`r2.shard<K>of3` with artifacts `.assay/verdict-r2.r2.shard<K>of3.json` /
`.assay/progress-r2.r2.shard<K>of3.jsonl` and store
`.run-gate/assay-shards/<project>/r2/r2/<K>of3` (a remote clone has no
`.worktrees/` prefix, so `<project>` is `scripts/debian-install-v2`). Each remote
chunk passes `--collect-store`, which packs that store into `assay-store.tgz`
before the throwaway checkout dies; nothing else preserves it.

## 4. Merge on one host: the binding verdict

`--merge-shards 3` never fabricates a store; Assay's strict
`--merge-shard-store` refuses absent, duplicate, foreign-judge, changed-plan,
incomplete or polluted stores. Unpack all three archives into one directory, then
seed the INNER path (tar members keep their `<rel>/` prefix).

```bash
SP=scripts/debian-install-v2; mkdir -p /tmp/v2-merge
for a in <shard0 run> <shard1 run> <shard2 run>; do tar -xzf $a/assay-store.tgz \
  -C /tmp/v2-merge; done   # then: ls /tmp/v2-merge/.run-gate/assay-shards/$SP/r2/r2/
scripts/controller/remote-gate.sh r1002 $SP r2 <sha> --base fdf60ffdc \
  --seed-store /tmp/v2-merge/.run-gate/assay-shards/$SP=.run-gate/assay-shards/$SP \
  -- --merge-shards 3
```

The MERGE run's `r2` verdict binds the commit; per-shard verdicts are inputs.

## 5. Resume: prove it resumed

State lives outside the judged worktree and dies with the throwaway checkout, so
a later chunk of one shard is seeded from that shard's own archive, same
`--shard K/3` and `--base`. The proof is the progress stream, never the verdict:
`resumed_total` / `rejected_total` exist only there
(`assay/src/assay/mutation.py:3052-3069`), so `grep '"event": "resume"'` over
`.assay/progress-r2*.jsonl` must show `resumed_total > 0`. A judge mismatch is a
cache miss, not an error (the candidate re-executes, counted as rejected).
`resumed_total: 0` with `rejected_total: 186` (B577) was this lane's old failure:
a `{worktree}` PYTHONPATH folded the checkout path into `judge_sha256`. The env is
now checkout-RELATIVE (`PYTHONPATH = ".:../../libraries/cowtree/src"`, `PATH` only
in `env_passthrough`). `BUDGET_EXCEEDED` (exit 4): resume.

## 6. What a survivor means here

`installer.py:807`/`:811` (`return ""` whose caller tests truthiness), `:4109`
(`mounted` is reassigned on both paths) and `layout_policy.py:236` (the only
`DataVolume` whose `nocow` is read always passes it) are EQUIVALENT **by proof,
not waived** (`../CHANGES.md`); a NEW survivor needs killing tests, then a re-run.
