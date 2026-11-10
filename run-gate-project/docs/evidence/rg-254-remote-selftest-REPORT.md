<!-- origin: 2026-11-09; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/test-policy-doc-REPORT.md -->
<!-- relation: RG-254 remote-selftest failure evidence -->

# Estate test-run policy documentation report

## Status

Documentation is complete in worktree `/workspaces/vbpub/.worktrees/test-policy-doc-20261109`.
Final head: `b8c00b6226a7cd20f790f8dedba52427a4104608` on
`test-policy-doc-20261109`. The worktree is clean. Fresh `origin/main` was
fetched and merged; it was already up to date. No code or tests were added to
the repository; the required registered gate was run as recorded below.

## Required inputs and verified evidence

Read the binding operator rules and requirements, both gate-reuse reports, the
in-review RG-253 design, Run-Gate consumer/spec/lane-authoring docs, Assay
consumer/design docs, CMRU release docs, and the real `run-gate.toml` and
`assay.toml` layouts for CMRU, CIU, Assay, Augeaswrap, Run-Gate, cli-extended,
pwmcp, topos, iobench and mounttrace. Also reviewed `libraries/test-policy/`
and its gate/Assay/CMRU configs.

Key verified facts recorded in the policy document:

- CMRU `gate-provisional` measured **2,989.8 s** total; pytest reports
  **741.17 s** inside the coverage interval. Per-lane durations are inferred,
  not directly instrumented. Four unmodified full-suite controls plus two
  transformed canary runs are present.
- A T2 projection near **990 s** is conditional on two exact eligible PASS
  records (Assay and full-coverage lane), RG-253 support, the coverage artifact
  contract, and no changed release-prep path read by reused lanes. The
  uncovered-line canary remains independent. The review supports at most about
  **2,000 s / 66.9%** savings, not 97.6%.
- RG-253 is still in review on another branch. Run-Gate history is telemetry,
  not PASS authority; RG-253 v1 does not map an Assay record into a separate
  coverage lane. Reuse is documented as proposed until the design is
  implemented and released.
- CMRU's suite reads `CHANGES.md`, so a changed prep-only path cannot be
  silently excluded from reuse. The policy requires path-reader analysis.
- Current lane names do not align uniformly: CMRU's `gate-provisional` omits
  R2 while `gate` includes it; CIU separates `ciu-gate` and `ciu`; several
  projects' `gate` lanes include R2; the Run-Gate project's release pointer
  still selects `selftest` while `gate-full` includes R0/R1/R3.
- `libraries/test-policy` is an unreleased project for test reliability
  tooling, not a central policy authority. Its current `gate` includes R2.
- Fuzzing has no standard target/corpus schema, remote dispatcher or
  automatic backlog/R4 conversion in the current Run-Gate/Assay support.

## Documentation delivered

- [TEST-POLICY.md](/workspaces/vbpub/.worktrees/test-policy-doc-20261109/run-gate-project/TEST-POLICY.md)
  defines T0–T3, the seven recommended presets, per-project mappings and
  accepted deviations, duplicate-run claim attribution, fuzz lifecycle, the
  read-only `run-gate policy-check` contract, a ten-project audit checklist,
  measured/estimated timing tables, and seven open operator decisions.
- [CONSUMERS.md](/workspaces/vbpub/.worktrees/test-policy-doc-20261109/run-gate-project/CONSUMERS.md)
  and
  [LANE-AUTHORING.md](/workspaces/vbpub/.worktrees/test-policy-doc-20261109/run-gate-project/LANE-AUTHORING.md)
  link to the policy document.
- The `[policy]` table and `policy-check` are explicitly proposals; no new
  CLI or parser behavior was added.
- Claims cite source file and line locations. Unsupported project mappings and
  timings are marked `UNVERIFIED`.

## Policy decisions and open questions

The document follows the binding event schedule: T0 focused; T1 pre-merge R0
+ changed-line R1; T2 release R0/R1/R3 with only eligible exact PASS reuse and
declared release-prep-only paths; T3 asynchronous R2, fuzz and optional
reviewer-directed R4. R2 and fuzz are nonblocking for release. `gate` names the
release-safe T2 conjunction; `gate-provisional` records local T3 follow-up
semantics but still must prove T2. Existing local names are retained and
audited against their actual contents.

Seven questions remain for the operator, also listed in the doc:

1. Should T2 R1 always mean whole-project coverage, or can a project declare a
   narrower release scope?
2. Which Run-Gate release and date make RG-253 reuse available for policy
   claims?
3. Which tracked paths may each project declare release-prep-only, and which
   checks read each path?
4. Should CMRU retain both independent R3 mechanisms?
5. Should accepted deviation evidence be stored in TOML or only in the reviewed
   PR/decision record?
6. Which projects must declare fuzz targets/corpora before the fuzz preset is
   considered available?
7. Should any repeat fuzz finding require a red-first R4 test, or remain the
   reviewer's call?

## Measured and estimated time expectations

The doc separates measured totals, inferred per-lane intervals and projections.
Besides CMRU, retained measurements include Assay self-qualification at
1,370.3 s and 1,300.2 s; pwmcp R1 at 397.5 s and 391.6 s; topos at 110.9 s;
and Run-Gate build/receipt at 116.9 s followed by selftest at 246.1 s. No
comparable time series was available for CIU, Augeaswrap, cli-extended, iobench
or mounttrace, so those are marked `UNVERIFIED`. T0 and T3 estate-wide estimates
are also marked unverified.

## Mutation accounting

No executable guard or behavior changed. Hand mutation was not applicable.

## Gate and clean-head evidence

The required gate was `run-gate selftest` from
`run-gate-project/`, under the mandated lock and priority:

```text
flock <controller-gate-lock> nice -n 10 run-gate selftest
```

The first invocation was mistakenly started at the worktree root, which has
no root `run-gate.toml`; it exited 2 before running tests. The correct
project-directory invocation used the registered `tester-unified` lane on a
clean tree. Its Docker label was
`run-gate.commit=b8c00b6226a7cd20f790f8dedba52427a4104608`, matching final
HEAD; `.run-gate/history.json` also records the same commit.

The gate **failed**: **1 failed, 3,079 passed, 1 skipped in 346.74 s**. The
failure was
`TestStallEndToEndCommandLane.test_a_silent_command_lane_is_stopped_with_evidence_and_error_status`
at `run-gate-project/tests/test_run_gate.py:22174`; `_direct_child_pids()`
observed `[16708]` after the fake Docker log-stream stall path. The fixture
uses a child subreaper and performs cleanup in `finally`. The output did not
identify PID 16708's process name, so its exact origin is **UNVERIFIED**. No
test rerun or code edit was made; this task is documentation-only, and the
failure is outside the changed files. The `&&` command did not proceed to the
coverage-gate step after pytest failed.

Gate logs:

- `/tmp/run-gate/lanes/selftest/fd01e38c613ab54798ade7cb9af281ce.log`
- `/tmp/run-gate/vbpub-run-gate-run-gate-project-selftest-b8c00b62-261109-2144-962166885.log`

The selftest lane includes documentation contract tests in
`run-gate-project/tests/test_run_gate.py:1387-1522` (documented examples,
vocabulary, cross-document anchors and related text contracts).

## Commit and trailer verification

Task commit: `b8c00b6226a7cd20f790f8dedba52427a4104608`.
`git log origin/main..HEAD` contains this one commit. A mechanical per-commit
check confirmed the exact trailer on every commit:

```text
b8c00b6226a7cd20f790f8dedba52427a4104608 PASS
```

Trailer checked:
`Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>`.
