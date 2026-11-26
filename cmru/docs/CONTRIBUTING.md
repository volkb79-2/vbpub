# Working on cmru — things that cost someone real time

`SPEC.md` is normative: it says what cmru MUST do. This file is the other half —
what actually bites you while changing it. Add to it when something costs you an
hour.

**Two lessons found here were general, so they live where every project sees
them, not in this file:**

* **A check is only as strong as what it actually compares** — the
  narrower-than-its-message defect shape, with the four instances this repo
  produced: estate `AGENTS.md`.
* **Read the exit status from the job, never the wrapper**: estate `AGENTS.md`.
* **Why coverage, mutation and Assay's import-break canary catch disjoint defect classes**, and why
  some surviving mutants are a defect in the code's *shape* rather than a gap in
  the tests: `assay/docs/DESIGN-GUIDE.md` §3a, with the how-to in
  `assay/docs/CONSUMERS.md`.

What follows is cmru-specific.

---

## 1. cmru gates itself with a mutation campaign — run it before you believe you are done

cmru's own gate is **100% line and branch coverage from Assay, plus a
changed-source mutation campaign**. Coverage is the cheaper, weaker signal. A
change set here reached a passing suite at 100% statement *and* branch coverage while the campaign found **six surviving
mutants** — one in the single line deciding whether a pinned version was stale.

From the repository root, run the registered release gate:

```bash
run-gate gate
```

The gate prepares authenticated origin baseline facts on the host, then runs
the campaign in `tester-unified`; the publisher token is not forwarded into the
test container. Run `mutation` directly only when you have supplied fresh
`CMRU_ASSAY_BASELINE_FACTS` from the host preflight. The known-good full-suite
control has a 600-second no-output window; candidates use
`min(600, max(300, 2 * control_longest_output_gap_upper_bound))` seconds,
including the 30-second sampling resolution. The longest concrete silent
subprocess timeout found in the tracked test suite is 300 seconds
(`cmru/tests/test_release_end_to_end_real_git.py:212`). There is no elapsed-time
limit: a child that keeps printing can run indefinitely. The tool samples
output growth at most every 30 seconds with `os.fstat`; it prints a
still-running line only after output grows. Memory-pressure credit follows
Run-Gate RG-320, capped so the effective window plus polling and cleanup stays
below the R2 lane's 15-minute `stall_timeout`. A control ended for silence
produces inconclusive evidence (`baseline_exit_code: 124`,
`baseline_termination: no-output`), exits 2, and maps to Run-Gate `ERROR`; it
has no candidate judgment and must be retried. A candidate ended for silence
is recorded in its own `outcome: timeout` bucket, never as killed or survived,
and makes the shard and any merge fail. Both `mutation` and `r2` run the
campaign setup test with `PYTHONUNBUFFERED=1`. The standalone T3 `r2` lane has
no total wall budget; the aggregate `[lanes.mutation]` lane retains its
separate 7-hour budget. The tool's silence bound applies to both lanes. The
runner updates
`.assay/mutation-cmru.json` and appends per-candidate events to
`.assay/progress-mutation-cmru.jsonl`; another run resumes from completed killed
results and retries survivors or interrupted candidates. **Read the evidence
file rather than reasoning about a survivor:** it records each mutation's path,
operator, exact `Op->Op` description, outcome, and termination reason.

### Split, collect, resume, and merge the T3 R2 lane

The lane accepts zero-based `--shard INDEX/COUNT`, partitioned round-robin over
the full ordered Assay candidate list. Forward it through Run-Gate after its
argument separator. The lane accepts no extra arguments or exactly one valid
`--shard K/N`; it rejects `--help`, `--base`, `--evidence`, and extra
separators before its source checks, Assay installation, or setup tests:

```bash
run-gate --base cmru-v6.1.0 r2 -- --shard 0/3
```

Each shard's `mutation-cmru.json` has `scope: shard`, `status: partial`, the
full candidate manifest, and results only for its assigned indexes. A shard's
Run-Gate PASS is partial; the merged evidence file is the only whole-change R2
record. Use the same base and candidate commit for all three shards.
The evidence JSON is unsigned and does not establish who produced it.

For remote hosts, collect the ignored `.assay` store explicitly. `remote-gate`
archives the run's own checkout before removing it; the generic artifact bundle
includes `progress-mutation-cmru.jsonl` but omits `mutation-cmru.json`.

```bash
BASE=$(git rev-parse --verify 'cmru-v6.1.0^{commit}')
SHA=$(git rev-parse --verify 'HEAD^{commit}')
scripts/controller/remote-gate.sh <host> cmru r2 "$SHA" \
  --collect-store cmru/.assay -- --bas "$BASE" -- --shard 0/3
```

`remote-gate.sh` generates the run name and collects and verifies
`assay-store.tgz` during that invocation; use the printed local output
directory as `<run-dir-0>`. `--bas` is intentional: the wrapper intercepts an
exact `--base` option and the remote runner appends that base after the lane
arguments, while Run-Gate requires the base before the lane-argument separator.
Repeat the command for shard indexes `1/3` and `2/3`, keeping the same base and
candidate commit. The fetched archive keeps its `cmru/.assay/` prefix. Extract
each archive into a separate local store directory so shard evidence files do
not overwrite one another:

```bash
mkdir -p <store-root>/0
tar -xzf <run-dir-0>/assay-store.tgz -C <store-root>/0
```

Repeat extraction for runs 1 and 2. To resume a stopped shard, seed its
extracted `.assay` directory back to the same target, keep the same shard
argument, base, and candidate commit, and collect again:

```bash
scripts/controller/remote-gate.sh <host> cmru r2 "$SHA" \
  --seed-store <store-root>/0/cmru/.assay=cmru/.assay \
  --collect-store cmru/.assay -- --bas "$BASE" -- --shard 0/3
```

After collecting all three complete files, merge them from the repository root:

```bash
mkdir -p cmru/.assay
python3 cmru/tools/mutation_campaign.py --merge-shards 3 \
  --shard-evidence <store-root>/0/cmru/.assay/mutation-cmru.json \
  --shard-evidence <store-root>/1/cmru/.assay/mutation-cmru.json \
  --shard-evidence <store-root>/2/cmru/.assay/mutation-cmru.json \
  --merge-output cmru/.assay/mutation-cmru-merged.json
```

The merge refuses a missing or duplicate shard, an incomplete assignment, or
any difference in HEAD, base, manifest, or campaign identity. Its output is the
only evidence with `scope: whole`; a surviving, crashed, or timed-out mutant
makes the merged campaign fail. Resume accepts a descendant HEAD only when
the campaign identity, source and test closure, fixture, configuration,
Assay source, silence policy, and recorded bounds still validate; it reuses
only validated killed outcomes. A local Run-Gate session may delegate command
lane liveness to cgprofile instead of watching log output, but CMRU's own
silence bound applies in either mode. `remote-gate cancel` stops before store
collection, so use it only when the current shard's on-host evidence can be
discarded; an ordinary lane exit, including `stall_timeout`, reaches the
collect-store step.

## 2. The default dry-run uses the `origin/main` snapshot

By default, `release` fetches `origin/main`, creates a worktree at **that exact
remote commit**, and re-execs there. That is deliberate: a release must never
publish from a dirty caller tree.

The consequence is easy to miss: **`cmru release --dry-run` from your branch
uses the default `origin/main` source**, so it does not preview unpushed or
uncommitted changes from the caller.

To preview a committed, pushed feature branch with the KI-82 candidate, install
the CMRU checkout under review into the active development environment, ensure
its same-named `origin/<branch>` is at `HEAD`, and run
`cmru release --from-worktree --dry-run` from that clean branch. This option is
available only when the installed CLI lists it; its registered gate is still
pending. Unpushed or uncommitted CMRU changes still require the installed
operator workflow below. Module aliases such as `python -m cmru.cli` are
intentionally unsupported:

```bash
python3 -m pip install --editable ./cmru
cmru status       --config ./cmru.orchestration.toml
cmru dependencies --config ./cmru.orchestration.toml
cmru tool-deps    --config ./cmru.orchestration.toml
```

and call the guarded plan computation directly — see
`version.detect_changed_projects`'s release-path keyword arguments.

## 3. A gate step copied from `cmru.toml` — export the orchestration `[env]` first

The `argv` in a project's `[steps.*]` depends on environment the
**orchestration** layer injects from `cmru.orchestration.toml`'s `[env]` — not
the project's own `cmru.toml`. So a step copied out by hand — what you do when a
release goes red — needs that `[env]` block exported first; §1's snippet carries
the current set.

**KI-17 is fixed** (SPEC S2.6a): `tester-gate` now validates the whole required
set up front and, if anything is missing, aborts **once, naming every missing
variable together**, before any container spin-up — no more failing one variable
at a time. Each message now names `cmru.orchestration.toml [env]` (inherited
through `cmru release`) as the real source instead of pointing at the wrong
file. Exporting the `[env]` block by hand is still the way to reproduce a step
standalone; the tool just tells you the complete list in one shot now.
