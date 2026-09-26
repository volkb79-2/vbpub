# B105 self qualification — implementation log

**Status:** ACTIVE. The package is implementing the B105 backlog acceptance
recorded at `4-backlog.md:10653` on current main
`94009fbba09c798d8759dce5c4039db2ddb2b196`.

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
  `assay-b105-self-qualification` there at then-current `main`
  `456164d528b11adea23b8812094bb510d4d5cfb4`. Its worktree is
  `/tmp/vbpub-b105-ciu-root-20260926/.worktrees/assay-b105-self-qualification`.
  Reachable Git objects have since been repacked into the clone's own object
  store, and the external alternate was removed. Its eventual branch can be
  fetched into the primary repository for serial merge.
- The CIU worktree was first created at `456164d5`; before final qualification,
  the branch was rebased onto current `origin/main` `94009fbb` (a CMRU
  release-input documentation change) with no conflicts. The final source
  qualification therefore includes the latest main history.

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
  `tester-unified` as R0-only. Its R1 declaration literally names the 50
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

The initial candidate-count and shard checks completed before the first gate
attempt. The rebased `f23bc6b1` preflight now establishes the 100% whole-source
R0/R1 result. The final full R0-R3 run, retained report/log, backlog closeout,
review, and serial merge remain open.

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

### Gate attempt 3 — 2026-09-26

- An earlier full-lane attempt on source commit
  `a7542820e2a19cbf4065bc02825d03e1f3583300` (tree
  `338a9b5cc9707f67b02fb323ee9d53a2f4153bc2`) completed its baseline in
  10m12s: 5,065 passed, 21 skipped, and 3 failed. The command imported Assay
  from the mounted source tree rather than a built wheel, so the R0 tests that
  require wheel provenance failed. R1 also refused `EXCLUDED_LINES` at
  12,257/13,174 executable statements and 5,215/5,910 branch arcs, with 66
  excluded lines, 917 missing statements, and 695 missing arcs. R2 was
  `COMMAND_FAILED` for missing `judge_provenance`; no candidate campaign ran,
  and R3 was inconclusive.
- The B105 driver now builds the selected exact-OID source as a wheel in a
  private clone, installs it into the run venv, requires wheel provenance, and
  keeps R0/R1 preflight ahead of R2. This failed attempt remains under
  `.assay/verdict-self-qualification.json`,
  `.assay/progress-self-qualification.jsonl`, and
  `/tmp/run-gate/run-gate-vbpub-b105-ciu-root-20260926-self-qualification-201605-1790392180.log`.

### Gate attempt 4 — 2026-09-26

- The R0/R1 preflight on `f1850f21` completed the suite in 8m27s but correctly
  failed at R0: the real-descendant cleanup test read `/proc/<pid>/stat` after
  the child had exited, and the file read raised `ProcessLookupError` between
  the path lookup and read. The same report identified six uncovered
  executable lines and seven missing branch arcs; coverage was not yet
  release-acceptable. Its artifacts remain in `.assay/` for commit `f1850f21`.
- The cleanup probe now treats both `FileNotFoundError` and
  `ProcessLookupError` as an already-exited child. Added boundary cases for
  the uncovered runner paths, and routed normal mutation outcome recording
  through the same classifier used for witness eligibility so equivalence and
  kill-signal rules have one implementation. A focused debug run passed 48
  tests. The branch was then rebased onto current main before the next gate.

### Gate attempt 5 — 2026-09-26

- The registered `self-qualification-preflight` lane passed in
  `tester-unified` on source commit `f23bc6b19716f360ecd2145eddb05f683fa30ff5`
  (tree `c21be711556c34b4f217de0317df59f2cb85847c`); the gate's `assay verify`
  step accepted its R0/R1 verdict. Its exact whole-target inventory is 50
  source files, matching all 50 production Python files under `src/assay`.
  Coverage was 13,175/13,175 executable statements and 5,892/5,892 branch
  arcs, with no missing lines or branches. Thirteen explicitly marked
  non-executable lines remain excluded as recorded by the raw coverage
  report; no source file was omitted. The baseline completed in 8m26s.
- Retained preflight outputs are under the worktree's ignored
  `assay/.assay/`: `verdict-self-qualification-preflight.json`,
  `progress-self-qualification-preflight.jsonl`, and
  `coverage-self-qualification-preflight-snapshot.json`. The complete
  run-gate invocation/output is `/tmp/b105-self-qualification-preflight-f23.log`.
  The final full qualification gate will repeat this preflight on its own
  exact source commit before starting native R2.

### Final candidate plan — 2026-09-26

- On clean source commit `1ee6832e14d0a98d4a8da12c9f0d962977ea08d4`,
  `assay plan self-qualification --file assay.toml` returned `status=ok`,
  `candidate_count=3760`, `jobs=1`, and `shard=null`. The operator counts are
  `compare-swap=2169`, `boolop-swap=958`, `bool-const-flip=498`, and
  `falsy-swap=135`; all planned candidates are below the configured
  `max_mutants=10000` ceiling. The planner estimated 225,600 serial seconds
  (about 62h40m) using the current automatic per-candidate estimate. This is a
  forecast, not a lane deadline or evidence of elapsed campaign time; the
  self-qualification lane has an unbounded campaign budget and the full gate
  will verify the actual complete candidate inventory.

### Stopped full attempt on e79eb8f5 — 2026-09-26

- The controller's full `self-qualification` run used source commit
  `e79eb8f507d2060ff1429d1ea13ed3fe671f21da` (tree
  `a43d50644256f78c610dd14ced0a4b3a4e85ac29`) in the CIU worktree
  `assay-b105-self-qualification`. The repeated R0/R1 preflight passed and
  `assay verify` accepted its report. The native campaign plan contained
  3,760 candidates; the progress stream recorded 39 candidate events before
  the controller deliberately stopped the run after 38 candidate completions
  to investigate evidence-integrity defects. `assay analyze progress`
  confirmed the captured stream belongs to e79 and records the 3,760-candidate
  campaign. This did not complete R2, run R3, or produce final accepted
  qualification evidence.
- The detached gate's actual exit was 143. Its run-gate log is
  `/tmp/b105-self-qualification-e79.log`; the detailed run-gate evidence is
  `/tmp/run-gate/run-gate-vbpub-b105-ciu-root-20260926-self-qualification-903325-1790419726.log`.
  The reported 987 MiB peak and 20-second memory-full stall are measurements
  from the stopped run, not test criteria or a verdict.

### Controller evidence-integrity remediation — 2026-09-26

- A GPT-6-Sol xhigh review found that mutant pytest sessions could replace the
  retained preflight coverage artifact and that the outer gate did not bind a
  passing verdict to the captured source and lane. The full mutation lane now
  receives no B105 coverage-export variables. Each preflight gets a persistent
  reserved attempt directory; its raw coverage filename includes the exact
  source commit and tree, timestamp-only repeats preserve the first raw report,
  and changed evidence cannot replace it. This keeps corrected commits and
  retries separate.
- The external checker now validates the expected commit/tree, lane, declared
  rigor, PASS outcome and claims, producer exit, Assay version, and wheel
  digest. The controller runs it from the private exact-OID source clone, and
  the final guard checks HEAD, its tree, and a clean worktree.
- The positive report fixtures pass Assay's verifier for both R0/R1 and
  R0-R3; the nonzero-producer test starts from a verifier-accepted PASS report.
  Focused checks passed: `python -m pytest tests/test_self_lane.py
  tests/test_b105_report_check.py -q` (24 passed), `git diff --check`, shell
  syntax, and TOML parsing. A fresh GPT-6-Sol xhigh follow-up found no
  actionable findings. It noted that the private-clone invocation and final
  worktree guard are currently covered by source assertions; the registered
  non-R2 gates remain pending. No final B105 pass is claimed.
