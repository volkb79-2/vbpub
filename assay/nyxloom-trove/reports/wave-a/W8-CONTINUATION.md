# W8 continuation (checkpoint after step R)

Worktree `/workspaces/vbpub/.worktrees/wave-a-w8-measurement` (project dir `assay/`), branch `wave-a-w8-measurement`, base `bdd9cde7`.
Read first: `W8-measurement.md` (brief), `CARVER-DECISIONS.md`, `b110/P0-measurement-hygiene.md` (imported subsections only: W5 refusals 1-9 and step 5 / O14, W6 G1-G5), `W8-LOG.md`.

## Done (green, committed)
- L `eee1de96`, S `9675b112`, R `73003664` (see LOG table for oracles and controlled breaks).

## Remaining, in order
1. **P+H** (brief "P", "H"; tests O-P1..O-P5, O-H1; T8 wheel list if exact).
   - New `analysis/src/assay_analysis/plan_estimate.py`, exact imports/constants/messages per brief section P; `analysis/src/assay_analysis/cli.py` (`plan-estimate` subparser, `_workers`, `cmd_analyze` branch, exit 2 for `report` and `plan-estimate`), `__init__.__all__ = ["cli","evidence","plan_estimate"]`, add module to `[lanes.analysis]` `judge.targets` in `assay.toml` (sorted). Tests `analysis/tests/test_analysis_plan_estimate.py`; analysis lane must stay 100% (`nice -n 19 ionice -c3 python -m pytest analysis/tests -q -p no:cacheprovider --cov=analysis/src/assay_analysis --cov-branch`). Also check `ALLOWED_JUDGE_MODULES` in `analysis/tests/test_analysis_package_boundary.py` (plan_estimate imports no judge module).
   - H in `src/assay/cli.py` `_cmd_plan`: `tree` via `git.run(..., "rev-parse", f"{commit}^{{tree}}", remaining=deadline.remaining).strip()` after `commit = git.head_rev(...)`; `ok` payload gains `commit`,`tree`; `PLAN_ESTIMATE_HINT` constant; `_cmd_plan(args, out, err=None)`; `main` passes `err`; hint to stderr only when status ok. Test `tests/core/test_cli_plan_estimate_hint.py` (O-H1). The four `cli._cmd_plan(..., io.StringIO())` calls in `tests/core/test_b105_cli_boundaries.py` stay unmodified. Judge code: 100% line+branch, no pragma.
2. **C** `tools/b105_report_check.py` (`--plan-json`, `check_campaign_scope`, refusals 1-9, refusal-9 addition per CD41 `ValueError("plan commit/tree differ from the expected source")`, order per brief), `tools/self-qualification-gate.sh` `run_and_verify_lane` (plan step before run, `--plan-json "$plan_path"`); tests O11 in `gate/tests/test_b105_report_check.py` (+ CD41 case), O12 pins in `gate/tests/test_self_lane.py`, O14 in new `gate/tests/test_b105_report_check_real_plan.py` (setup exactly as brief). Re-resolve anchors by content (W4 rewrote the checker: receipt, `--receipt-only`; W2 added `verify_scope`).
3. **G** `tests/core/test_isolation_guards.py` (G1-G5 per P0 W6; controlled breaks O13: `os.link` in `_copy_objects`, G3 test-local reuse).
4. **Docs**: README (:52-58 analysis block, :975 B105), CONSUMERS (:2307-2312 upper-bound fix, progress table :2648/:2652, plan examples, `commit`/`tree`), DESIGN-GUIDE (:484 B107, :1972 B105, :3406 why analysis), CHANGES (exact lines in brief Docs step; name every CD38-amended additive surface), B111 status line in `nyxloom-trove/4-backlog.md`. Never mention `--baseline-from`, `estimate_provenance`, `full_suite_central_*`.
5. Finish: one full `tests` coverage run, one `analysis/tests` coverage run, collect counts for `tests`/`analysis/tests`/`gate/tests`, pragma check, `git status --short --ignored assay` (delete the ignored `assay/.coverage`), complete LOG, `READY-FOR-GATE <hash>`.

## Exact next command
`cd /workspaces/vbpub/.worktrees/wave-a-w8-measurement/assay && nice -n 19 ionice -c3 python -m pytest analysis/tests/test_analysis_package_boundary.py -q -p no:cacheprovider` (orient on W2's package), then create `plan_estimate.py`.

## Load-bearing seams (current file content)
- `src/assay/liveness.py`: `_PidStat`/`_pid_stat`/`_walk_tree`/`tree_sample`; `read_resource_sidecar`, `first_event_times`, `baseline_phase_durations` (after `_finite_number`); `LivenessRunner._monitor` (sidecar `finally`) -> `_poll`; pragma at line 94 must not move.
- `src/assay/mutation.py`: `_MutantRun` + `_measured_resources`; `_run_attempt` phase stamps; worker loop `resources = {...}` before the `candidate` event and state record. Pragmas 148/155/156 must not move.
- Editor tools only (Edit/Write). No sed, python rewrite scripts, or shell appends into repo files. Never run `run-gate.py`.
