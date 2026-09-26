# B105 self qualification — implementation log

**Status:** ACTIVE. The package is implementing the B105 backlog acceptance
recorded at `4-backlog.md:10653` against main `456164d528b11adea23b8812094bb510d4d5cfb4`.

## Baseline and worktree

- `git log --all --grep='B105'` found no implementation commit; the backlog
  still marks B105 OPEN and the shipped `assay.toml` release lane still declares
  R0 only.
- CIU's repo-wide worktree validation refused creation in the primary checkout
  because `.worktrees/rg55-p6-r2-ciu/ciu.worktree-instance.json` claims branch
  `rg55-p6-r2-ciu` while Git registers that unrelated checkout as detached. I
  left that RG-55 checkout and record untouched.
- A temporary clone at `/tmp/vbpub-b105-ciu-root-20260926` isolates CIU's
  instance registry. CIU created logical worktree and branch
  `assay-b105-self-qualification` there, pinned to the exact main SHA above.
  Its worktree is
  `/tmp/vbpub-b105-ciu-root-20260926/.worktrees/assay-b105-self-qualification`.
  Reachable Git objects have since been repacked into the clone's own object
  store, and the external alternate was removed. Its eventual branch can be
  fetched into the primary repository for serial merge.

## Planned construction

- Preserve `tester-unified` as the fast R0 release gate and add a distinct,
  discoverable B105 self-qualification lane.
- Enumerate every production `src/assay/**/*.py` file explicitly in the
  `whole_target` R1/R2 declaration. Add a shipped-loader test that compares
  that inventory with the current source tree, so new production files cannot
  silently fall outside the denominator or mutation plan.
- Run the declared pytest suite at R0; require whole-source line and branch
  coverage at R1 with a 100% floor; run native Python R2 over the complete,
  unsharded source inventory; and run the declared import-break R3 canary with
  its control and transformed attempts.
- The B105 gate will execute inside `tester-unified`, invoke Assay with
  `--resume --progress .assay/progress-self-qualification.jsonl`, preserve the
  verdict under ignored `.assay/`, and run `assay verify` on that same verdict
  before it reports success. The full gate log and verifier-accepted verdict
  will be retained with the source revision after the gate runs.

## Live record

Implementation, focused checks, plan estimate, complete R0-R3 gate, artifact
verification, review, and merge results will be appended below as they occur.

### Initial implementation — 2026-09-26

- Added `self-qualification` as a second `assay.toml` lane while retaining
  `tester-unified` as R0-only. Its R1 declaration literally names the 52
  production Python files under `src/assay`; the self-lane test compares that
  list with the live source inventory.
- Registered `./run-gate.py self-qualification` as a tester-unified command.
  Its driver installs the selected worktree's source, invokes `assay run` with
  `--resume`, `.assay/progress-self-qualification.jsonl`, and a persistent
  `.assay/mutation-state` directory, verifies the emitted report, and checks
  HEAD/tree identity before printing its success marker.
- Declared whole-target R1 at 100% with required branch measurement, all four
  Python native operators at `jobs = 1` with no sharding, explicit `auto`
  per-candidate bounds and liveness, plus a bounded `import-break` canary on
  `src/assay/cli.py`.
- Added A-462, updated README/DESIGN-GUIDE/CONSUMERS, and aligned Assay's
  Nyxloom gate registry. The initial controller timeout and run-gate advisory
  budget were 96 hours; attempt 2's 8m49s full-suite run showed the planner's
  60-second-per-candidate fallback was not a reliable campaign runtime
  estimate. The controller watchdog and advisory budget are now 90 days; the
  Assay lane itself remains unbounded, and a controller timeout cannot accept
  partial evidence.
- Source-backed `assay lanes --file assay.toml` passed. `./run-gate.py --list`
  lists `self-qualification` in `tester-unified`. `bash -n` and
  `git diff --check` passed. After implementation commit `774d99bb`,
  `assay plan self-qualification --file assay.toml` returned `status=ok`,
  `candidate_count=3774`, `jobs=1`, and `shard=null`. By operator inventory:
  `compare-swap=2180`, `boolop-swap=961`, `bool-const-flip=498`, and
  `falsy-swap=135`; the inventory is below the 10,000 ceiling and is not
  sharded. The planner estimated `226440` serial seconds (about 62h54m),
  using its 60-second-per-candidate fallback because no campaign baseline had
  yet been measured. This is a forecast, not elapsed-time evidence or a gate
  deadline.
- Repacked reachable Git objects into the temporary CIU clone and removed its
  object-store alternate. A targeted connectivity check for main tip
  `456164d5` then passed, so the tester-unified container will not depend on
  mounting `/workspaces/vbpub/.git/objects` from outside the selected clone.

The candidate-count and shard checks completed before the first gate attempt.
R1's 100% measurement, the full R0-R3 run, retained report/log, and serial
merge remain open.

### Gate attempt 1 — 2026-09-26

- The first registered gate started in `tester-unified` on source commit
  `322459e2640ff393d46db994381cc1c94033c44e` (tree
  `f8a92178583e617c13af2dccf883333f3beca824`) and stopped at the baseline in
  about seven seconds, before coverage or mutation work. Its verdict was
  verifier-written `NO_MEASUREMENT/EMPTY_COVERAGE`; the captured pytest stderr
  was `/usr/local/bin/python: No module named pytest`.
- The failure came from the lane resolving bare `python` on tester-unified's
  ambient PATH instead of the declared gate interpreter. A detached,
  cgroup-placed probe confirmed `/opt/tester-venv/bin/python` has pytest 9.1.1
  and coverage.py 7.16.1, while ambient `python` is `/usr/local/bin/python`.
  The gate driver now puts the tester interpreter's bin directory first on
  PATH before invoking Assay; this is the PATH that the lane explicitly
  passes through. The failed verdict and progress stream were saved under
  `/tmp/b105-gate-attempt-1-20260926/` for diagnostic retention.

### Gate attempt 2 — 2026-09-26

- The interpreter fix reached and completed the full baseline pytest command
  in 8m49s: 5,053 passed, 21 skipped, 17 failed. The failures all read
  historical project commits that Assay's default shallow snapshot omitted;
  the suite's parent-repository guard did not skip because the isolated tree
  is itself a repository. The baseline's coverage file contained no data
  because `--override-ini=pythonpath=` caused imports to resolve through the
  editable package in the invoking worktree, outside the measured snapshot.
  R1 correctly refused `BRANCH_UNAVAILABLE`; R2 did not start.
- The lane now explicitly uses `snapshot_history = "full"`, sets
  `pythonpath=src` so each baseline/mutant imports its own snapshot source,
  and deselects only the two tag-ref audit tests because snapshot materializes
  commit history but intentionally does not preserve refs/tags. The ordinary
  checkout-based release lane continues to run those two tests. A drift test
  pins the exact history policy, import path, and deselections. The failed
  verdict and logs were saved under `/tmp/b105-gate-attempt-2-20260926/`.
  The outer controller watchdog and run-gate advisory budget were also widened
  together to 90 days. Assay's lane budget remains `unbounded`; reaching the
  controller watchdog is an incomplete gate, never an R2 pass.
