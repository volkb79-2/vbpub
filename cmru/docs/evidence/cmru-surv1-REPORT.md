<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/cmru-surv1-REPORT.md -->
<!-- relation: CMRU R2 survivor closeout; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# CMRU v7.2.2 R2 shard s1 closeout

**Status:** The s1 fixes and follow-up are committed. The registered full first-release lane ran once and failed on the pre-follow-up commit; focused post-fix checks pass. The full lane was not rerun under the one-run instruction, so the final commit does not have a full-lane PASS result.

- Branch: `cmru-surv1-20261106`
- Implementation commit: `ef293876b8fb29a1bbc426b33616e1ecc3cdebb4`
- Follow-up commit: `0e2b0ad238091d12f368c5c0e5a61cffb1850c49`
- Campaign: `cmru-r2c-s1`, source commit `37ea9ad87`
- Result: 304 candidates, 248 killed, 56 survived
- Evidence: `<controller-program-dir>/<host>/r2-closeouts-20261105/cmru-r2c-s1/`; board report section 12 in `<controller-program-dir>/reports/r2-board-20261105b-REPORT.md`

## Root-cause disposition

The 56 survivors are itemized in [`cmru/KNOWN_ISSUES_TODO_BACKLOG.md`](../../KNOWN_ISSUES_TODO_BACKLOG.md) under KI-129. Fifty-three were behavioral gaps in validation, immutable records, subprocess flags and diagnostics, serialization, or Run-Gate preflight reporting. Three were equivalent/dead-code cases and were removed or simplified at source. Each new behavioral oracle was checked with its corresponding mutation hand-applied in a disposable scratch worktree.

The closeout removed `src/cmru/__init__.py` from both whole-target first-release Assay lanes. It contains only a docstring, so Assay refused to measure it (`TARGET_NOT_MEASURED`). The new inventory test derives executable Python modules from AST and requires both target lists to match that inventory.

Review found an incomplete identity fallback: Run-Gate preflight could report PASS with `sha256 unknown`. It now requires a 64-character lowercase wheel digest and rejects missing or malformed values.

The full run also found two lane/test defects. The estate import oracle read `mdt-cockpit/build-push.py` from a shared COW overlay instead of the judged commit; it now reads `git show HEAD:mdt-cockpit/build-push.py` and checks the `run_step` import structurally. R1 also rejected three excluded `TYPE_CHECKING` lines in `tool_deps.py` under its strict whole-target policy; `ToolDependency` is now imported at runtime. These corrections are in the follow-up commit.

## Verification

- `run-gate survivor-regressions --allow-dirty` on the implementation commit: **PASS**, 50 passed in 1.40s.
- Post-fix focused `run-gate survivor-regressions --allow-dirty -- tests/test_tool_deps.py tests/test_w2_integ.py::test_estate_python_callers_import_only_what_cmru_exports`: **PASS**, 90 passed in 15.35s.
- Scratch probes: **55 probe invocations, 56 expected-red checks** (the 53 non-equivalent survivors, wrong-target inventory control, and missing/malformed digest controls). Logs and summary: `<temp-path>/`.
- `assay-first-release` ran once on `ef293876b8fb29a1bbc426b33616e1ecc3cdebb4`: **FAIL**. R0 failed at `tests/test_w2_integ.py::test_estate_python_callers_import_only_what_cmru_exports` because the COW overlay contained stale sibling-project source; pytest reported 6,414 passed, 6 skipped, and 1 failed. R1 reported 100% line and branch coverage but failed with `EXCLUDED_LINES` for `tool_deps.py` lines 63–65. R3 was inconclusive because the baseline did not pass. The corrected import oracle and runtime type import were then checked with the focused registered gate above. The full lane was not rerun.
- Full-lane log: `<run-gate-state>/vbpub-run-gate-cmru-assay-first-release-ef293876-261106-1324-951694162.log`; verdict: `cmru/.assay/verdict-cmru-first-release.json`.
- Static checks: `git diff --check`, TOML parsing, Python AST parsing, and the 56-row backlog count passed. The final worktree is clean.

No push, merge, tag, release, host access, or real release machinery in tests was used. Independent review remains the next step.

## Fix round — 2026-11-07

**Status:** Review findings are addressed. The final registered first-release lane passes on the committed tree.

- Final HEAD: `b3ace5dc15074dad9b3a7c6ac65b66f4b0d6c9e1` (`fix(tester-unified): wait for wheel identity parser`), branch `cmru-surv1-20261106`.
- The `assay-first-release` lane ran locally through Run-Gate with `tester-unified:local`, nice 10, and the declared 3-CPU cap. Verdict: **PASS**, exit 0. R0 passed; R1 passed at **100%**: 16,666/16,666 executable lines and 6,952/6,952 branches across 40 judged files; R3 passed: control PASS and the `config.py` import-break transform was rejected with the expected `COMMAND_FAILED` result and matched receipt. The successful verdict retains no pytest pass/skip split; a registered collect-only invocation recorded **6,424 test items**.
- R0 ran inside Assay's materialized Git snapshot. B101's shallow-snapshot behavior was confirmed in Assay source/docs: the snapshot has `.git`, `HEAD` points at the judged commit, and the shallow seed carries the complete tree. The `git show HEAD:` import oracle passed in the final R0 run, so it remains hermetic without history or refs.
- The full-lane profiler noted a quiet canary with no progress event for 307.4 seconds while CPU grew 5.9 seconds in the preceding 30 seconds, plus a 67.2-second memory-stall interval excluded from sizing. The canary completed and the Assay verdict remained PASS.
- Focused post-fix gate: `nice -n 10 run-gate survivor-regressions --allow-dirty -- tests/test_r2_survivors_s1.py tests/test_tester_unified_image.py` — **PASS, 594 passed**. The delayed-parser regression verifies the digest-refusal path waits for and reaps its helper.
- F1 now parametrizes a missing version, `""`, and integer `24`. In the hand-applied `or`→`and` probe, the missing-value case still passed because `None` is rejected by both expressions; the empty-string and integer cases failed, killing the mutant. The three cases are necessary to exercise both operands of the guard.
- KI-129 now records 0-based token columns for same-line mutants. The `survivor-regressions` default selector uses `tests/test_r2_survivors_s*.py`, so later shard closeouts can add files without editing the lane.
- The earlier R0 teardown error was a process-substitution helper left alive after the main shell refused a bad digest. The identity parser now runs synchronously, and the delayed-child test covers the early-refusal path.
- R2 campaign totals remain **304 candidates: 248 killed, 56 survived**; this fix round did not rerun the campaign.
- Integration note: `cmru-r2-shards-20261105` still lists `src/cmru/__init__.py` in both first-release lanes (at lines 114 and 195). Drop it from both lane target lists when that branch is integrated; that branch was not edited here.

Final worktree is clean. No push or merge to the shared `main` checkout was performed.
