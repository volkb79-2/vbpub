# R2 runbook: three shards, one merge, resume

`[lanes.r2]` is a NATIVE Assay lane (`kind = "assay"`, RG-265 sharding); judge = the judged
worktree's own `assay/` source (RG-285), base from the request; policy `assay.toml [lanes.r2]`.

## 1. Plan before you run (never guess the split)

```bash
cd scripts/debian-install-v2 && PYTHONPATH=$PWD/../../assay/src \
  /opt/mdt/venv/bin/python3 -m assay.cli plan r2 --file assay.toml \
  --request-base fdf60ffdc
```

Measured 2026-11-26: whole **194** of 320; shards `0/3` **58**, `1/3` **72**, `2/3` **64**.
`assay plan` refuses an uncommitted `assay.toml`.

## 2. Preconditions: one commit, one image, one `--jobs`

ONE commit throughout: a store carries its plan. The judge is not only the tree: `judge_sha256`
folds a fingerprint of every PRESENT `env_passthrough` value (`mutation.py:1603-1611`; this lane
passes `PATH`) and `resource_sha256` folds `jobs` and the memory budget (`_judge_projection.py:255`).
Every shard, merge and resume host needs the SAME `tester-unified` image (its `PATH` is part of the
judge identity) and the SAME `--jobs`, or the merge sees a foreign judge:

```bash
scripts/controller/hx.sh r1002 'docker image inspect tester-unified:local -f "{{.Id}}"'
scripts/controller/hx.sh v1001 'docker image inspect tester-unified:local -f "{{.Id}}"'
```

This `2g` lane takes 2 of a host's 4 admission slots: three shards need two hosts. A host without a
Cowtree btrfs cheap-clone workspace refuses `--jobs 2` by name (`--jobs 1` there also moves
`resource_sha256`; only its `crashed`/`hung`/`budget_exceeded` records then re-execute).

**Read the verdict in `lane.log`.** `remote-gate.sh` anchors on the REQUESTED lane (`:291`), a shard
prints `run-gate: lane 'r2.shard<K>of3' verdict ...` (`run_gate.py:474`), so the anchored read finds
nothing and exit code plus slice `oom_kill` delta decide: a shard that legitimately ended
`BUDGET_EXCEEDED` (exit 4) while ANY kill happened is `INCONCLUSIVE-OOM`, exit 76 (`:305`), and §5's
"exit 4: resume" never runs.

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

Each shard is instance `r2.shard<K>of3` (verdict `.assay/verdict-r2.r2.shard<K>of3.json`, progress
`...shard<K>of3.jsonl`) with store `.run-gate/assay-shards/<project>/r2/r2/<K>of3` (`<project>` =
`scripts/debian-install-v2` remotely); `--collect-store` packs it before the checkout dies.

## 4. Merge on one host: the binding verdict

`--merge-shards 3` never fabricates a store; Assay's strict `--merge-shard-store` refuses absent,
duplicate, foreign-judge, changed-plan, incomplete or polluted stores. Unpack all three archives into
one directory and seed the INNER path (members keep their `<rel>/` prefix; the stores are disjoint
`<K>of3` leaves). `--store-max-mib` bounds each collected directory AND the seed -- here the COMBINED
seed; 512 MiB default, far above these stores.

```bash
SP=scripts/debian-install-v2; mkdir -p /tmp/v2-merge
for a in <shard0> <shard1> <shard2>; do tar -xzf $a/assay-store.tgz -C /tmp/v2-merge; done
scripts/controller/remote-gate.sh r1002 $SP r2 <sha> --base fdf60ffdc \
  --seed-store /tmp/v2-merge/.run-gate/assay-shards/$SP=.run-gate/assay-shards/$SP \
  -- --merge-shards 3
```

The MERGE run's `r2` verdict binds the commit; per-shard verdicts are inputs.

## 5. Resume: the killed chunk, and the `budget_exceeded` rejudge

State dies with the checkout, so a later chunk is seeded from that shard's own archive, same
`--shard K/3` and `--base` (unpack locally first; seed target = the INNER path). PLAIN form -- a
KILLED chunk:

```bash
RG="scripts/controller/remote-gate.sh"; SP=scripts/debian-install-v2
mkdir -p /tmp/v2-s0 && tar -xzf <shard 0/3 run>/assay-store.tgz -C /tmp/v2-s0
$RG r1002 $SP r2 <sha> --base fdf60ffdc \
  --seed-store /tmp/v2-s0/.run-gate/assay-shards/$SP=.run-gate/assay-shards/$SP \
  --collect-store .run-gate/assay-shards/$SP -- --shard 0/3
```

It reuses `killed`/`survived` while `judge_sha256` is unchanged. `crashed`, `hung` and
`budget_exceeded` are RESOURCE-sensitive (`mutation_store.py:595-600`), re-run only when
`resource_sha256` moved -- so "resume" alone cannot get past a `BUDGET_EXCEEDED` shard: the record
is REUSED (`:4264-4274`), the shard ends `LANE_TIMEOUT` again (`mutation.py:5604-5605`), and the
strict merge counts it a PRESENT record. Add the rejudge flags instead:

```bash
$RG r1002 $SP r2 <sha> --base fdf60ffdc \
  --seed-store /tmp/v2-s0/.run-gate/assay-shards/$SP=.run-gate/assay-shards/$SP \
  -- --shard 0/3 --rejudge <id> --rejudge-outcome budget_exceeded
```

`--rejudge-outcome` drops every resumed record of that bucket before the store is consulted (a union
with `--rejudge`). Run-Gate accepts it only with at least one `--rejudge ID`
(`run_gate.py:21136-21137`), so take ONE `budget_exceeded` id from the shard verdict
(`claims[rigor=R2].mutation.budget_exceeded[0].candidate_id`).

The proof is the progress stream, never the verdict: `resumed_total` / `rejected_total` exist only
there (`mutation.py:3052-3069`), and the collected copy lives in the RUN directory, not the checkout
(`<out-dir>` = `$CONTROLLER_HOME/remote-gates` from the wrapper, `remote-gate.sh:225`; packed at
`remote-gate.py:760-762`; `:280` copies only `verdict*.json`). It is COMPACT json:

```bash
grep '"event":"resume"' \
  $CONTROLLER_HOME/remote-gates/<run>/artifacts/.assay/progress-r2.r2.shard<K>of3.jsonl
```

It must show `resumed_total > 0`; a judge mismatch is a cache miss, not an error. `resumed_total: 0`
with `rejected_total: 186` was this lane's rejection on 2026-11-25, at a head whose `PYTHONPATH` was
ALREADY checkout-relative (B577 records the cause as not identified); the earlier 14-record
rejection was `2ea1912ca`'s absolute `{worktree}` `PYTHONPATH`, folded in via `env_passthrough`.

A CANCELLED or killed remote run packs NO store and NO artifacts: `collect_stores` and
`artifacts.tgz` run only after the lane returns, while a TERM/INT enters the cancel trap and the EXIT
trap removes the checkout (`remote-gate.py:624-626`, `:634`, `:760-766`) -- that shard starts again
at candidate #1.

## 6. What a survivor means here

`installer.py:807`/`:811` (`return ""` whose caller tests truthiness), `:4109` (`mounted` is
reassigned on both paths) and `layout_policy.py:236` are EQUIVALENT **by proof, not waived**
(`../CHANGES.md`); a NEW survivor needs killing tests.
