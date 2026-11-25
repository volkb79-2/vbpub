# R2 runbook: diff-scoped mutation on two hosts

The `r2` lane mutates **only the lines a comparison base..HEAD diff adds** under
`src/iobench`, and runs 3 workers inside a 4 GiB cap. This page is the operator
procedure for one owed R2 (a changed package) — not for a whole-package
campaign, which is `r2-whole` (section 7).

Why the plain lane could not deliver it: `r2` used to delegate to the
whole-target Assay lane, which plans **1891** candidates for this package,
and its 1536m cap produced `N=1`; the 2026-11-24 run judged 75 candidates in
6 h (0.2 cand/min) and ended `BUDGET_EXCEEDED` — an ETA of about 8450 min.
See the [design rationale](DESIGN-GUIDE.md#registered-repository-gate).

## 1. How it works (file:line)

* **A request base reaches the judge only through `base_source`.** Run-Gate
  appends Assay's `--request-base <ref>` for a lane whose inventory says
  `base_source == "request"` (`run-gate-project/run_gate.py:15018`, guard
  `:9972-9979`). Our lane declares it in
  `assay.toml` `[lanes.iobench-diff.judge]` (line 133).
* **A native Assay lane can carry it** — converting `r2` to a command lane is
  unnecessary. (`scripts/debian-install-v2/run-gate.toml:69-77` is a command
  lane only because it drives Assay by hand; its `assay.toml:33` still carries
  `base_source = "request"`.)
* **Two lanes exist because one lane cannot be both.** Assay refuses
  `judge.base_source` under `mode = "whole_target"`
  (`assay/src/assay/config.py:2874-2880`) and `judge.targets` under
  `changed_lines` (`:2894-2899`). `base_source = "request"` is also what makes
  a base *required from the request*: without `--base` the lane does not fall
  back to HEAD or a default branch.
* **Candidate discovery** is the diff's added lines under `source_roots`
  (`assay/src/assay/runner.py:5701-5720` → `_mutation_targets_from_diff`
  `:4521`; the whole-file path is `_mutation_targets_whole` `:4578`).
* **Sharding exists and is hash-based, not per-file.** `--shard K/N`
  (`assay/src/assay/cli.py:444`, parse `:4202`) assigns a candidate by
  `blake2b(candidate_id, 4) % N` (`assay/src/assay/mutation_store.py:2609-2639`).
  There is **no by-module split**; the supported way to steer a candidate set
  is `assay plan --list-pending` piped into `assay run --only-candidates`
  (`assay/src/assay/cli.py:619-641`, `:568`).
* **Run-Gate's native shard campaign (RG-265)** runs instance
  `<lane>.shard<K>of<N>` with its own store
  `.run-gate/assay-shards/<project-rel>/<lane>/<assay_lane>/<K>of<N>`, and
  `--merge-shards N` merges them with Assay's STRICT `--merge-shard-store`
  (`run-gate-project/SPEC.md:3083-3105`; `run_gate.py:14650-14702`, `:14769`,
  14824-14837). The judge floor is Assay >= 12.2.0, proven by
  `--merge-shard-store` in the inventory probe (`run_gate.py:14652`,
  `:14999-15004`).
* **Budget margin**: the wrapper lane budget must exceed the Assay lane's own
  budget by >= 5 minutes (`run_gate.py:217`, check `:20133-20149`).
  `150m` outer > `145m` inner satisfies it.

## 2. The measured plan (reproduce, do not guess)

```bash
cd libraries/iobench
PYTHONPATH=$PWD/../../assay/src /opt/mdt/venv/bin/python3 -m assay.cli plan \
  iobench-diff --file assay.toml --request-base 94d9c2227
```

Measured 2026-11-25 on base `94d9c2227` (the pre-merge base of WP-10 P1),
head `ba2581e39`:

| View | Candidates |
|---|---|
| whole plan | **98** / 1200 `max_mutants` |
| `--shard 0/2` (r1002) | **53** |
| `--shard 1/2` (v1001) | **45** |

By file: `host.py` 43, `testing.py` 37, `guard.py` 7, `stages.py` 6,
`devio.py` 3, `cli.py` 1, `restore.py` 1.
By operator: `compare-swap` 64, `bool-const-flip` 18, `boolop-swap` 11,
`falsy-swap` 5.

Against the whole-target lane that is 98/1891 ≈ 5 % of the work, and the 43
`host.py` candidates are the M1 raw-claim core the owed R2 exists for.

`assay plan` refuses to run while `assay.toml` is uncommitted
(`assay/src/assay/runner.py:3663`): commit first, or you are reading a plan
for a policy the judge will not use.

## 3. Run both shards (the controller does this)

Both commands are the estate wrapper (`scripts/controller/remote-gate.sh`),
which bundles the commit and the base to the host, runs the lane in a
throwaway checkout and pulls the artifacts and the store back. Run them from
the repository root of the commit under test.

```bash
# shard 0/2 on r1002: 53 candidates
scripts/controller/remote-gate.sh r1002 libraries/iobench r2 HEAD \
  --base 94d9c2227 \
  --collect-store .run-gate/assay-shards/libraries/iobench \
  -- --shard 0/2

# shard 1/2 on v1001: 45 candidates
scripts/controller/remote-gate.sh v1001 libraries/iobench r2 HEAD \
  --base 94d9c2227 \
  --collect-store .run-gate/assay-shards/libraries/iobench \
  -- --shard 1/2
```

`--base 94d9c2227` is mandatory on a remote host: there is no upstream there
and no CIU fork-point record, so without it the lane answers
`NOT_RUN` / `no-base` instead of guessing (that refusal is the design, not a
missing default).

`-- shard K/2` reaches `run-gate` unchanged. `remote-gate.sh` also accepts
plain run-gate arguments after `--` — that is where `--jobs 1` belongs if a
host lacks a Cowtree btrfs cheap-clone workspace (jobs > 1 is refused by name
without one, `run_gate.py:5397-5406`); the shard lane keeps `jobs = 3` otherwise.

After both finish, merge on one host (strict: every shard store must exist):

```bash
scripts/controller/remote-gate.sh r1002 libraries/iobench r2 HEAD \
  --base 94d9c2227 \
  --seed-store <local merged dir>=.run-gate/assay-shards/libraries/iobench \
  -- --merge-shards 2
```

The local merged dir is the two collected archives unpacked side by side so
that it holds `r2/iobench-diff/0of2` (from r1002) and `r2/iobench-diff/1of2`
(from v1001) under one `libraries/iobench` directory:

```bash
mkdir -p /tmp/iobench-merge && tar -xzf <r1002 run>/assay-store.tgz -C /tmp/iobench-merge \
  && tar -xzf <v1001 run>/assay-store.tgz -C /tmp/iobench-merge \
  && ls /tmp/iobench-merge/.run-gate/assay-shards/libraries/iobench/r2/iobench-diff/
```

`--merge-shards` never fabricates a missing store — absent,
duplicate, foreign-judge, changed-plan, incomplete or polluted stores are
refused before Assay starts (`run-gate-project/SPEC.md:3083-3105`).

Without `--seed-store` the merge refuses with the exact paths it wants, e.g.

```
--merge-shards 2: shard store(s) missing:
<checkout>/.run-gate/assay-shards/libraries/iobench/r2/iobench-diff/0of2, .../1of2;
run `run-gate r2 --shard K/2` for each (or copy the remote host's store there) first
```

## 4. Budgets and the arithmetic

Inputs from the 2026-11-24 measurement (`rgw-gate-20261124T164429-v1001`,
head `74e86a3f5`, lane memory 1536m, `jobs 1`): baseline (the R0 pytest run)
253-270 s; candidate wall p50 278 s, p90 330 s, max 408 s; 0.2 candidates/min
at one worker (about 278 s each, i.e. candidates ran back to back, so one
worker ≈ 0.2/min).

* **Per candidate**: `budget_per_candidate = "12m"` (720 s). It is 1.76x the
  worst measured candidate and 2.2x the p90: a ceiling for a slow or
  contended mutant, not a target. `"auto"` derived 760 s
  (`max(3 x baseline, baseline + 60)` in
  `assay/src/assay/mutation.py:2136-2153`) from the 253 s baseline and would
  follow that baseline up under three-way contention.
* **Per shard lane**: the usable candidate window is
  `145m - baseline - closeout`. Baseline 270 s + setup ≈ 5 min; the closeout
  analysis (Assay `analyze campaign`) gets a quarter of what remains per call,
  capped at 900 s per call, and is skipped under 40 s
  (`run_gate.py:15138-15160`), so reserve 8 min. That leaves about
  **132 min** of judging per shard.
* **Throughput at N=3**: not measured for this lane. At N=3 on 3 CPUs the
  workers contend, so do not assume 3x. A conservative planning factor is
  **1.5x the one-worker rate** (0.3 cand/min), which is 0.1 cand/min per
  worker; ideal scaling would be 3x. With the conservative factor one chunk
  covers about `132 x 0.3 = 40` candidates.
* **Fit**: shard 0/2 plans 53 and shard 1/2 plans 45, so each host needs
  **two chunks** at the conservative factor and one if scaling is better than
  1.5x. Resume, do not enlarge blindly: stop at `BUDGET_EXCEEDED` and re-run
  the same command with the seed store (section 5). Raising `145m` is also
  safe up to the wrapper's `150m + 5m` rule, but a chunk that ends on its
  budget is not a failure.
* **Lane memory**: 4 GiB declared, 75 % of it (3072 MiB) is the memory budget
  (the default `ram_target = "75%"`, `run_gate.py:2165`), and
  `candidate_ram = 512m` (a floor over the measured
  p95 391,901,184 B ≈ 374 MiB and the known 1,462 MiB maximum) gives
  `memory_workers = 3072 // 512 = 6` (`run_gate.py:5389`); CPU cap 3 with
  oversubscribe 1.0 gives
  `cpu_workers = 3`; `jobs = min(jobs_max 4, 3, 6, remaining) = 3`
  (`run_gate.py:5342-5405`). 3 x 512 MiB is inside the cap with room for the
  baseline; the 2026-11-02 campaign ran N=3 and peaked at 2.67 GiB on the
  slice.

## 5. Resume and store hygiene

* State is written **outside the judged worktree** at
  `<checkout>/.run-gate/assay-state/<project-rel>` (or
  `.../assay-shards/<project-rel>/<lane>/<assay-lane>/<K>ofN` for a shard),
  and it dies with the throwaway checkout. Every remote chunk therefore
  passes `--collect-store .run-gate/assay-shards/libraries/iobench`, which
  packs that directory into `assay-store.tgz` + `.sha256` before the checkout
  is removed; a later chunk passes
  `--seed-store <collected dir>=.run-gate/assay-shards/libraries/iobench`.
* A seeded resume is **the same judge** only if `judge_sha256` does not fold a
  checkout path in. `assay/src/assay/mutation.py:1514-1560` folds the lane's
  `env_declared` values and the SHA-256 fingerprint of every *present*
  `env_passthrough` value. This lane declares
  `PYTHONPATH = "src:../cli-extended/src"` and `env_passthrough = ["PATH"]` —
  both relative/ambient, no `{worktree}` anywhere — so a resume across
  checkouts matches (contrast assay B577 / run-gate RG-314). Verify it
  actually resumed: the run's `resumed_total` must be > 0, not just the same
  verdict.
* `--store-max-mib` defaults to 512 MiB of uncompressed tar bytes
  (`scripts/netcup/runner/remote-gate.py:90`); raise it if a store grows past
  it, and never point `--collect-store` at the whole `.run-gate` tree.

## 6. Reading the result

* Verdict: `.assay/verdict-iobench-diff.<lane>.shard<K>of2.json` in the
  checkout, plus `run-gate: lane '<lane>' verdict ...` in `lane.log`.
  `remote-gate.sh` copies every `verdict*.json` beside the log as
  `<run>.verdict-N.json`.
* Survivors are rows in that verdict. File them against the package; they are
  not fixed inside an R2 campaign.
* `BUDGET_EXCEEDED` (run-gate exit 4) is not a defect of the lane: resume
  with the seed store (section 5).

## 7. The whole-target lane (`r2-whole`)

Note for the next docs package: `docs/CONSUMERS.md` "Repository gate" and
`docs/DESIGN-GUIDE.md` "Registered repository gate" still describe `r2` as
the whole-target `jobs = "auto"` / `1536m` lane. Those files were outside
this package's allowed-file list; the text above is correct for the shipped
configuration.

`run-gate r2-whole` is the previous behaviour, byte for byte: every shipped
module, all 1891 candidates, `jobs = "auto"`, 1536m, 365m. `[lanes.gate]`
still runs it, so the release gate is unchanged; a periodic whole-package
campaign is still the only way to judge a file nobody touched in this change.
Use it deliberately (`run-gate r2-whole`), never as the lane for an owed R2 on
one diff.
