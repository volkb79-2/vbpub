# W2 - `assay analyze` as its own package (B127)

| Field | Value |
|---|---|
| Backlog | B127 (A-478). Wave A stage 1. |
| Branch | `wave-a-w2-analysis`, cut from `assay-b110-landing` after W3 merges. Merge back with `--no-ff`, then delete the branch. |
| Depends on | W3. W1 also edits the gate script and `test_distribution_gate.py`, so whichever of the two merges second rebases. |
| Contract class | 2d |
| Implementer | Sonnet (operator rule). Anything this brief leaves open is a carver question (end). |
| Decisions | A-478, A-005, A-402, A-462, A-476, A-468(c), A-479, A-129/A-182 |

Anchors are at `5bbd916e`, relative to `assay/`. If W3 moved a test, find it by filename.

## Context to read first
- `src/assay/analysis.py`. The judge names it uses are `__version__`, `git`, `AssayError`, `verify_text`, and `PROGRESS_EVENTS` (lazy, :704); none are private.
- `src/assay/cli.py` :104-134, :213-220, :455-492.
- `tools/b105_report_check.py`.
- `tools/tester-unified-gate.sh` :134-152, :302-362.

## Work
Use the editor tools only; move files with `git mv`.
1. **Layout.**
   - `src/assay/analysis.py` becomes `analysis/src/assay_analysis/evidence.py`. Its :1023-1130 go to a new `cli.py` next to it.
   - `tests/test_analysis*.py` (2 files) move to `analysis/tests/`.
   - New `analysis/src/assay_analysis/__init__.py`: a docstring plus `__all__ = ["cli", "evidence"]`. A file with no executable line is `TARGET_NOT_MEASURED` (`evaluate.py:1256`).
   - New `analysis/tests/analysis_support.py`: `PROJECT_ROOT` (`parents[2]`) and `JUDGE_VERDICT_FIXTURES` (the directory that holds `r0_pass.json`; treat it as data). Do not import from `tests/`. Add no `conftest.py`. Keep basenames unique across both trees.
2. **`evidence.py`.** Make the imports absolute: `from assay import __version__, git`, `from assay.errors ...`, `from assay.verify ...`. :704 becomes `from assay.mutation import PROGRESS_EVENTS`, still lazy. Nothing else changes.
3. **Analysis `cli.py`.** Imports: `assay.cli.AssayArgumentParser`, `AssayError`, `evidence`.
   - `build_parser()`: `prog="assay analyze"`, no `description=`, `dest="analysis_command"`, `required=True`, subcommands unchanged.
   - `cmd_analyze(args, *, stdout, stderr)`: the old body.
   - `main(argv, *, stdout, stderr)`: `return cmd_analyze(build_parser().parse_args(list(argv)), stdout=stdout, stderr=stderr)`.

   Always go through `evidence.<name>` so that patches still take effect.
4. **Judge seam (`src/assay/cli.py`).**
   - Delete :134.
   - :220 becomes `subparsers.add_parser("analyze", help="collect and inspect existing review artifacts")`. It stays the first subparser and gets no other kwargs; `add_help=False` would be an unkillable mutant.
   - Add:
     ```python
     def _run_analyze(argv: list[str], stdout: TextIO, stderr: TextIO) -> int:
         """(A-478) The only place the judge names assay_analysis; imported only for `analyze`."""
         from assay_analysis.cli import main as analyze_main
         return analyze_main(argv, stdout=stdout, stderr=stderr)
     ```
   - In `main`, right after `raw = ...`: `if raw[:1] == ["analyze"]: return _run_analyze(raw[1:], out, err)`.
   - :470 becomes `cli_argv, appended = _split_appended_argv(raw)`.
   - Delete :485-486.
5. **`pyproject.toml`.**
   - `[tool.setuptools] package-dir = {"" = "src"}`; `where = ["src", "analysis/src"]`. Package-data is unchanged (Q1).
   - pytest: `pythonpath = ["src", "analysis/src"]`, `testpaths = ["tests", "analysis/tests"]`.
   - Comment on the carver probe: the wheel holds both packages and their schemas, and no tests. Without `package-dir`, the egg-info goes to the root, which is unignored (DIRTY_TREE).
6. **`assay.toml`.**
   - Delete :124 and :235.
   - Append `[lanes.analysis]` (A-478), copying the preflight lane and its sub-tables (:186-282), changing only:
     - `argv = ["python","-m","pytest","analysis/tests","-q","--cov=analysis/src/assay_analysis","--cov-branch","--cov-report=json:.assay/coverage-analysis.json"]`
     - `env_passthrough = ["PATH"]`
     - `budget = "60m"` (the nyxloom timeout)
     - `snapshot_history = "shallow"`
     - `source_roots = ["analysis/src/assay_analysis"]`
     - `targets`: the 3 package files
     - `allow_excluded = false`
     - `artifact = ".assay/coverage-analysis.json"`
   - A lane has one `judge.mode` (`config.py:2247-2266`), so R2 changed-lines is Q2.
7. **`b105_report_check.py`.**
   - Add `B105_SOURCE_ROOT = "src/assay"`, `OUT_OF_SCOPE_BY_DECISION = {"analysis/src/assay_analysis": "A-478"}` and `PROJECT_DIR = Path(__file__).resolve().parents[1]`.
   - `verify_scope(document, *, repo_root, expected_commit)` runs after the tree check. It computes `prefix = PROJECT_DIR.relative_to(repo_root.resolve()).as_posix()`: `"."` means none, and `ValueError` means refused. It raises `ValueError` (exit 2) unless all of these hold:
     - (a) `judgment.resolved.source_roots == ["src/assay"]`.
     - (b) The tracked top-level entries of `<prefix>/src/` are exactly `["assay"]`; otherwise `unclassified package under src/: X`.
     - (c) Each out-of-scope path has a tracked `__init__.py`; otherwise `stale out-of-scope declaration`.
     - (d) No r1/r2/r3 target is out of scope (name it and A-478), and every r3 target is under `src/assay/`.
     - (e) The r1 targets, and r2's if present, equal the sorted tracked `*.py` under `<prefix>/src/assay/` (`git ls-tree -r --name-only`); name what is missing or extra.
   - Append ` scope=src/assay out_of_scope=analysis/src/assay_analysis:A-478` to the ACCEPTED line. No new flag.
8. **`test_self_lane.py`.** Add `"analysis"` at :40 and :88. Replace :128-135 with:
   - `src/` holds exactly `{"assay"}`, ignoring `*.egg-info` and `__pycache__`;
   - the declared targets equal the discovered `src/assay/**/*.py`, sorted;
   - no target is out of scope, and every out-of-scope path exists (take the constant from the checker via `importlib`).
9. **Gate script.**
   - After `run_self_hosted_lane`, add `run_analysis_lane "$worktree" "$scratch"` in the same shape. It runs `assay run analysis --file assay.toml --require-judge-provenance --resume --progress "$scratch/progress-analysis.jsonl" --verdict-json "$scratch/verdict-analysis.json"`.
     - On failure, print the `analyze verdict --format text` diagnosis to stderr and `return 1`.
     - Otherwise run `assay verify ... || die`, then `echo 'ASSAY_GATE_PHASE=analysis-lane-passed'`.
   - `run_lint_phase` also lints `$scratch/clone/assay/analysis/**/*.py`, and dies if it finds none.
10. **Tests.** Keep the moved assertions unchanged.
    - **T1 `test_analysis.py`.**
      - Import `evidence as analysis` from `assay_analysis` and `main` from `assay.cli`.
      - :58 uses `JUDGE_VERDICT_FIXTURES`; :993, :1006 and :1062 use `PROJECT_ROOT`.
      - The docs test (:1060-1084) calls `assay_analysis.cli.build_parser()` on `shlex.split(line)[2:]`; its negative is `["verdict","file.json"]`.
      - Framer: change the import only.
    - **T2 `test_analysis_cli.py` (new).** The headline test from `test_cli_lanes.py:36-49`: `collect --help` exits 0 and a missing argument exits 2. Also, `report --expected-commit <40a> --verdict l /nonexistent` exits 2 and prints an `evidence_error` JSON.
    - **T3 `test_analysis_package_boundary.py` (new).**
      - `ALLOWED_PRIVATE_JUDGE_NAMES: dict[str, str] = {}`.
      - An AST scan requires every `from assay[.x] import _n` and every `<judge module>._n` to be listed, and flags a tainted `from assay.mutation import _x`.
      - The lane is as in step 6: targets are the discovered files, argv has no `tests`, and the budget is the nyxloom timeout.
    - **T4 `test_cli_lanes.py:36-49`.** `["run","--help"]` exits 0 and `["run"]` exits 2, both with the headline. The top-level help lists `analyze`.
    - **T5 judge `test_cli_analyze_seam.py` (new).**
      - (i) With a fake `assay_analysis(.cli)` in `sys.modules` (via `monkeypatch.setitem`), `main(["analyze","report","--","x"])` returns the fake's value, and the fake got that argv and the same streams.
      - (ii) With `sys.modules["assay_analysis"] = None`: `main(["lanes"])` returns 0 from `PROJECT_ROOT`, and `main(["analyze","report"])` raises `ModuleNotFoundError`.
    - **T6.** `src/assay` has exactly one `assay_analysis` import, in `cli._run_analyze`, with a tainted negative. It goes in W3's import-contract test, or else in `test_dependency_purity.py`.
    - **T7 `test_dependency_purity.py`.** Add `assay_analysis` to `ALLOWED_ROOTS` and scan both packages. Pin `package-dir` and `where`. `standalone`'s `analyze --help` exits 0 with the headline.
    - **T8 `test_distribution_build_release.py` (`built`).**
      - The zipapp's `analyze --help` exits 0 with the headline on line 1; `analyze report ... /nonexistent` exits 2.
      - The wheel holds `assay_analysis/{__init__,cli,evidence}.py` and the 3 `assay/schemas/analysis-*`, and no `assay/analysis.py`, `analysis/` or `tests/`.
    - **T9 `test_b105_report_check.py`.**
      - Positive fixture:
        - `source_roots ["src/assay"]`, and no `resolved.base`;
        - a whole-target `r1` with the declared B105 targets;
        - on the full lane, the same `r2` (`verify.py:1254`), and r3 and canary targets of `src/assay/cli.py` (`verify.py:809-823`);
        - `verify_document == []` still holds.
      - Negatives: an analysis target (A-478 in the message); a missing `vocabulary.py`; `source_roots ["src"]`.
      - Hermetic, with a tmp repo holding a copy of the checker: `src/extra/` is unclassified; a missing analysis `__init__.py` is stale.
    - **T10 `test_distribution_gate.py`.** `analysis-lane-passed` is required and comes after `run_self_hosted_lane`. The lint scope and the fake clone include `analysis`. A planted finding there makes lint red.
11. **Docs.**
    - README :26-72: one wheel and zipapp; the package loads only for `analyze`; B105 covers only `src/assay`; analysis has its own R0+R1 lane.
    - DESIGN-GUIDE: `### Package boundary (A-478)` under :3406. Why: analysis reads evidence and never judges (317 of 3,760 B105 candidates). Also: the seam, the private-name rule, the checker, and no R2.
    - CONSUMERS :4021: `import assay.analysis` is gone.
    - CHANGES: an entry under Changed.

## Oracles
Record each controlled break in the LOG, red then green.

| # | Observable | Negative |
|---|---|---|
| O1 CLI unchanged | T1 passes unedited; the LOG shows each `--help` and `collect` output byte-identical before and after | Removing the `_run_analyze` call fails T1 |
| O2 Seam lazy and one-way; boundaries; A-005 | T3, T5, T6, T7 | An eager import, the `raw[:1] !=` mutant, or tainted sources fail them |
| O3 One wheel and zipapp | T7, T8 | Without `analysis/src` in `where`, T8 fails |
| O4 Own lane and rigor | T3; the gate prints `analysis-lane-passed` | Delete one branch-covering test (locally): coverage drops below 100% |
| O5 B105 scope named | Step 8, T9, T10 | The T9 and T10 negatives |
| O6 Judge floor holds without the analysis tests | Preflight passes; checker ACCEPTED | A line only analysis tests covered (e.g. `git.ignore_rule_source`) turns R1 red. Fix it with a judge test, never a pragma |

**§3b (pasted). An oracle must not contain any of the following.**
- **A (L20).** An assert after a `monotonic()+N` deadline or a `sleep(N)`, or on elapsed time or iteration counts. Wait on join/Event/queue, or remove the wait. A timeout is only a generous failsafe and never decides. A slow-host failure is a true red: fix the test; never widen the timeout or add CPU.
- **B.** An unrestored global (logging, `environ`, module attributes, singletons; PL7 §5). A monkeypatch on a `__getattr__` proxy; patch its owner (L19). A teardown that destroys instead of restoring. Use a fresh `tmp_path`. For a full-suite-only failure, ask what an earlier test left behind.
- **C.** A `pass` body, a "nothing raised" test, trivia (call counts, private attributes, logs) in place of the contract, or a weakened or deleted assertion.
- **D (L11).** A no-cover pragma; the token matches anywhere on a line. An excluded `except` body does not cover its clause. Restructure instead.
- **E.** Real network access, or `now()` in an asserted value. Inject the boundary.
- **F.** Predicted coverage or mutation numbers, or missing lines read from a report (its Missing column hides arcs). Assert the policy and run the tool.
- **Check:** could the result flip on a slower host, in another worker, or in another order?

## Scope
- **Touch:** `analysis/**`, `src/assay/analysis.py`, step 4 of `cli.py`, `pyproject.toml`, `assay.toml`, the 2 `tools/` files, T1-T10, the 4 docs, B127 status, `nyxloom-trove/reports/wave-a/W2-LOG.md`.
- **Forbid:** other judge modules; moving judge helpers; a shim; moving schemas (Q1); R2 (Q2); `build_release.py` (probed, not needed); `run-gate.toml`, `nyxloom.toml`, `self-qualification-gate.sh`.
- Needing more than 2 files outside this list triggers BLOCKED.

## Gate
1. Run serially: `nice -n 19 ionice -c3 python -m pytest analysis/tests <T4-T10 files> -q -p no:cacheprovider`.
2. `cd <worktree>/assay && python ./run-gate.py tester-unified`. Then, **in a separate step**, read `ASSAY_GATE_PHASE=analysis-lane-passed`, `ASSAY_GATE_CONTAINER_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE`. Never read them through a pipe tail.
3. `python ./run-gate.py self-qualification-preflight` (O6), which plan §4 allows.

**Known red.** `test_cli_run.py::test_run_liveness_classifies_a_thread_join_hang_as_hung` fails on main (a B107 regression, fixed by `612843ef`). Until that fix lands, a gate whose only failure is this test is acceptable. Record it in the LOG.

**Host-load rule (paste into every agent prompt):**
- the host is shared with a production game server;
- run everything under nice/ionice;
- run at most one gate container, and none while another session's gate runs (check `docker ps`);
- never run the full `self-qualification` lane;
- remove containers by exact name only.

Use editor tools only, and keep the docs in sync. Trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

## BLOCKED rule
If a named contract cannot be met, or the work needs a forbidden file: STOP. Write `BLOCKED: <reason>` to `W2-LOG.md`, commit, and exit. Do not improvise; BLOCKED counts as success. Triggers:
- `verify_document` refuses T9 for a reason other than scope;
- 100% R1 needs a pragma or a change in judge behavior;
- the wheel ships tests, or the egg-info leaves `src/`;
- W3 contradicts step 1.

## Review
A fresh-session adversarial review, never a fork, happens before landing. It adds one combined-axis attack (for example, the zipapp running `analyze record -- cmd --flag`).

## Carver questions (the defaults are implemented)
- **Q1: schemas.** Default: stay in `src/assay/schemas/`. Moving them changes a documented path (CONSUMERS.md:4152), which is a major version under A-479.
- **Q2: analysis R2.** Default: none, which leaves `evidence.py`'s mutants unjudged. Options:
  - an explicit whole-target lane (~317 candidates) with a generalized B105 driver;
  - B130 component R2.
- **Q3: private judge names.** Default: a closed allowlist, empty for now. Alternative: publish public names.
- **Q4.** Dropping the undocumented `import assay.analysis` is not consumer-visible, so 7.2.0 stays.
