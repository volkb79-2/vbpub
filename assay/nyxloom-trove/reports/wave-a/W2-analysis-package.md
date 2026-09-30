# W2 - `assay analyze` as its own package (B127)

| Field | Value |
|---|---|
| Backlog | B127 (A-478). Wave A stage 1. |
| Branch | `wave-a-w2-analysis`, cut from `assay-b110-landing` after W3 merges. Merge back with `--no-ff`, then delete the branch. |
| Depends on | W3 and W1. Cut W2 after W1 merges (both edit the gate script and `test_distribution_gate.py`; W2's `run_analysis_lane` goes where W1 deletes phases). If W1 is open, wait. |
| Contract class | 2d |
| Implementer | Sonnet (operator rule). Nothing is left open: the former carver questions are decided (end). |
| Decisions | A-478, A-005, A-402, A-462, A-476, A-468(c), A-479, A-129/A-182 |

Anchors are at `5bbd916e`, relative to `assay/`. If W3 moved a test, find it by filename.

## Context to read first
- `CARVER-DECISIONS.md` (this directory): CD13 (amended), CD15 (amended), CD16-CD19 and CD29 bind. Where a review fix text below and a CD conflict, the CD wins.
- `src/assay/analysis.py`. The judge names it uses are `__version__`, `git`, `AssayError`, `verify_text`, and `PROGRESS_EVENTS` (lazy, :704); none are private.
- `src/assay/cli.py` :104-134, :213-220, :455-492; `tools/b105_report_check.py`; `tools/tester-unified-gate.sh` :134-152, :302-362.

## Work
Use the editor tools only; move files with `git mv`.
1. **Layout.**
   - `src/assay/analysis.py` becomes `analysis/src/assay_analysis/evidence.py`. Its :1023-1130 go to a new `cli.py` next to it.
   - `tests/test_analysis*.py` (2 files) move to `analysis/tests/`.
   - New `analysis/src/assay_analysis/__init__.py`: a docstring plus `__all__ = ["cli", "evidence"]`. A file with no executable line is `TARGET_NOT_MEASURED` (`evaluate.py:1262-1270`).
   - New `analysis/tests/analysis_support.py`: `PROJECT_ROOT` (`parents[2]`) and `JUDGE_VERDICT_FIXTURES` (the directory that holds `r0_pass.json`; treat it as data). Keep basenames unique across both trees.
   - New `analysis/tests/conftest.py` (CD13): loads the fixtures the moved tests need from `tests/conftest.py` under a unique module name (`importlib.util.spec_from_file_location("assay_judge_conftest", ...)`, registered in `sys.modules`) and re-exports them. Load only what a moved test still needs (the FIFO test no longer takes `standalone`, see T1). W4 reuses this loader for `gate/tests/`.
2. **`evidence.py`.** Make the imports absolute: `from assay import __version__, git`, `from assay.errors ...`, `from assay.verify ...`. :704 becomes `from assay.mutation import PROGRESS_EVENTS`, still lazy. Delete the `AssayError` import: only `cmd_analyze` used it (an unused import turns the pyflakes phase red). Keep every other import.
3. **Analysis `cli.py`.** Imports exactly: `from __future__ import annotations`, `import argparse`, `import json`, `import subprocess`, `from pathlib import Path`, `from typing import TextIO`, `from assay.cli import AssayArgumentParser`, `from assay.errors import AssayError`, `from assay_analysis import evidence`. `cmd_analyze` reaches the private evidence helpers as `evidence._report_commit`, `_report_max_errors`, `_tester_run`, `_report_text`, `_output_location` and `_write_new`.
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
   - `[tool.setuptools] package-dir = {"" = "src"}`; `where = ["src", "analysis/src"]`. Package-data is unchanged (CD16).
   - pytest (CD15 amended; W4's drift test pins exactly these values; `gate/tests` stays out of `testpaths`): `pythonpath = ["src", "analysis/src"]`, `testpaths = ["tests", "analysis/tests"]`.
   - Comment: the wheel holds both packages and their schemas, no tests; without `package-dir` the egg-info lands at the unignored root (DIRTY_TREE).
6. **`assay.toml`.**
   - Delete :124 and :235.
   - Append `[lanes.analysis]` (A-478), copying the preflight lane and its sub-tables (:186-282), changing only:
     - `argv = ["python","-m","pytest","analysis/tests","-q","--cov=analysis/src/assay_analysis","--cov-branch","--cov-report=json:.assay/coverage-analysis.json"]`
     - `env_passthrough = ["PATH"]`
     - `budget = "60m"` (the nyxloom timeout)
     - `snapshot_history = "shallow"`
     - `source_roots = ["analysis/src/assay_analysis"]`; `targets`: the 3 package files; `allow_excluded = false`; `artifact = ".assay/coverage-analysis.json"`
   - A lane has one `judge.mode` (`config.py:2247-2266`), so there is no R2 lane (Decided, CD17).
7. **`b105_report_check.py`.**
   - Add `B105_SOURCE_ROOT = "src/assay"`, `OUT_OF_SCOPE_BY_DECISION = {"analysis/src/assay_analysis": "A-478"}` and `PROJECT_DIR = Path(__file__).resolve().parents[1]`.
   - `verify_scope(document, *, repo_root, expected_commit)` runs after the tree check. It computes `prefix = PROJECT_DIR.relative_to(repo_root.resolve()).as_posix()`: `"."` means none, and `ValueError` means refused. It raises `ValueError` (exit 2) unless all of these hold:
     - (a) `judgment.resolved.source_roots == ["src/assay"]`.
     - (b) The tracked top-level entries of `<prefix>/src/` are exactly `["assay"]`; otherwise `unclassified package under src/: X`.
     - (c) Each out-of-scope path has a tracked `__init__.py`; otherwise `stale out-of-scope declaration`.
     - (d) No r1/r2/r3 target is out of scope (name it and A-478), and every r3 target is under `src/assay/`.
     - (e) The r1 targets, and r2's if present, equal the sorted tracked `*.py` under `<prefix>/src/assay/`; name what is missing or extra.
   - Reading the tree: (b) uses `git -C <repo_root> ls-tree --name-only <expected_commit> -- <prefix>/src/` (trailing slash) and takes basenames. (e) uses `git -C <repo_root> ls-tree -r --name-only <expected_commit> -- <prefix>/src/assay/`, keeps the `*.py` names, and strips `<prefix>/` when `prefix != "."`. Never `rglob` the working tree.
   - Append ` scope=src/assay out_of_scope=analysis/src/assay_analysis:A-478` to the ACCEPTED line. No new flag.
8. **`test_self_lane.py`.** Add `"analysis"` at :40 and :88. Replace :128-135 with:
   - `src/` holds exactly `{"assay"}` (ignoring `*.egg-info`, `__pycache__`); declared targets equal the discovered `src/assay/**/*.py`, sorted; no target is out of scope and every out-of-scope path exists (constant taken from the checker via `importlib`).
9. **Gate script.**
   - Add this function verbatim and call it on the line directly after `run_self_hosted_lane "$worktree" "$scratch" "$version" "$wheel"` (PATH is already exported there). Do not call `require_emitted_*` for this verdict: `--require-judge-provenance` binds it.
     ```bash
     run_analysis_lane() {
       local worktree="$1" scratch="$2"
       cd "$worktree/assay"
       if ! assay run analysis --file assay.toml --require-judge-provenance \
           --resume --progress "$scratch/progress-analysis.jsonl" \
           --verdict-json "$scratch/verdict-analysis.json"; then
         echo 'ASSAY_GATE_DIAGNOSTIC=analysis-lane-red; inspecting its captured verdict' >&2
         assay analyze verdict "$scratch/verdict-analysis.json" \
           --expected-commit "$(git -C "$worktree" rev-parse HEAD)" --format text >&2 \
           || echo 'ASSAY_GATE_DIAGNOSTIC=captured-verdict-unavailable-or-invalid' >&2
         return 1
       fi
       assay verify "$scratch/verdict-analysis.json" || die 'assay verify refused the analysis lane verdict'
       echo 'ASSAY_GATE_PHASE=analysis-lane-passed'
     }
     ```
   - `run_lint_phase` also lints `$scratch/clone/assay/analysis/**/*.py`, and dies if it finds none.
10. **Tests.** Keep the moved assertions unchanged.
    - **T1 `test_analysis.py`.**
      - Import `evidence as analysis` from `assay_analysis` and `main` from `assay.cli`.
      - :58 uses `JUDGE_VERDICT_FIXTURES`; :993, :1006 and :1062 use `PROJECT_ROOT`.
      - The docs test (:1060-1084) calls `assay_analysis.cli.build_parser()` on `shlex.split(line)[2:]`; its negative is `["verdict","file.json"]`. Also change :1080 to `subcommands = parser._subparsers._group_actions[0].choices` (the new parser has no `analyze` level).
      - Framer: change the import only.
      - **FIFO test (CD13 amended).** `test_nonregular_inputs_refuse_without_waiting_for_a_fifo_writer` (:721) drops the `standalone` parameter. Its `subprocess.run` argv becomes `[sys.executable, "-c", "import sys; from assay.cli import main; raise SystemExit(main(sys.argv[1:]))", "analyze", *arguments]` with `env={**os.environ, "PYTHONPATH": os.pathsep.join([str(PROJECT_ROOT / "src"), str(PROJECT_ROOT / "analysis" / "src")])}` (absolute paths). Keep `capture_output=True, text=True, timeout=10, check=False` and both assertions. There is no `assay/__main__.py`, so no `-m assay`; the review's `-I`/`sys.path` variant loses to the CD (`-I` ignores `PYTHONPATH`).
    - **T2 `test_analysis_cli.py` (new).** The headline test from `test_cli_lanes.py:36-49`: `collect --help` exits 0 and a missing argument exits 2. Also, `report --expected-commit <40a> --verdict l /nonexistent` exits 2 and prints an `evidence_error` JSON.
    - **T3 `test_analysis_package_boundary.py` (new).**
      - `ALLOWED_PRIVATE_JUDGE_NAMES: dict[str, str] = {}` (CD18: it stays empty).
      - Scan every `analysis/src/**/*.py`: (1) track as judge modules every name bound by `import assay[.x] [as y]`, `from assay import x [as y]` and `from assay[.x] import y` where it is a module; (2) flag every `ImportFrom` from `assay[.*]` of a name matching `^_[^_]`; (3) flag every `Attribute` whose `attr` matches `^_[^_]` and whose value resolves to a judge module (including `evidence.<judge module>`); (4) flag `getattr(<judge module>, "_x")`; (5) allow private access only on `self`, `cls` and the sibling analysis modules. Negatives: `from assay.mutation import _x`, the alias form (`from assay import cli as c; c._x`), the dotted form (`assay.cli._x`) and the re-export chain (`evidence.git._x`).
      - The lane is as in step 6: targets are the discovered files, argv has no `tests`, and the budget is the nyxloom timeout.
    - **T4 `tests/core/test_cli_lanes.py:36-49`** (W3's path). `["run","--help"]` exits 0 and `["run"]` exits 2, both with the headline. The top-level help lists `analyze`.
    - **T5 judge `tests/core/test_cli_analyze_seam.py` (new).**
      - (i) With a fake `assay_analysis(.cli)` in `sys.modules` (via `monkeypatch.setitem`), `main(["analyze","report","--","x"])` returns the fake's value, and the fake got that argv and the same streams.
      - (ii) With `sys.modules["assay_analysis"] = None`: `main(["lanes"])` returns 0 from `PROJECT_ROOT`, and `main(["analyze","report"])` raises `ModuleNotFoundError`. (ii) does not prove laziness, because `assay.cli` is already imported; T6 does.
    - **T6, in `tests/core/test_import_contracts.py`** (W3's file; add it to the Touch list).
      1. Delete the line `if m == "assay.analysis": return "analysis"`, `ANALYSIS_DEPS`, the line `if ci == "analysis": return imported in ANALYSIS_DEPS`, and change `if ci == ct and ci != "analysis": return True` to `if ci == ct: return True`.
      2. In test (ii), replace the refused case `assay.verdict->assay.analysis` with `assay.verdict->assay.cli` (core must not import the composition root).
      3. Add `test_only_cli_run_analyze_imports_assay_analysis`: `ast.walk` over every `src/assay/**/*.py`, collecting each `Import`/`ImportFrom` of `assay_analysis` or `assay_analysis.*`; assert exactly one hit, in `src/assay/cli.py` inside `FunctionDef` `_run_analyze`. Tainted negative: the same checker fed a synthetic module-level `import assay_analysis.cli` refuses.
    - **T7 `test_dependency_purity.py`.** Add `assay_analysis` to `ALLOWED_ROOTS` and scan both packages. Pin `package-dir` and `where`. `standalone`'s `analyze --help` exits 0 with the headline.
    - **T8 `test_distribution_build_release.py` (`built`).**
      - The zipapp's `analyze --help` exits 0 with the headline on line 1; `analyze report ... /nonexistent` exits 2.
      - The wheel holds `assay_analysis/{__init__,cli,evidence}.py` and the 3 `assay/schemas/analysis-*`, and no `assay/analysis.py`, `analysis/` or `tests/`.
    - **T9 `test_b105_report_check.py`.**
      - Positive fixture: `source_roots ["src/assay"]`, no `resolved.base`; a whole-target `r1` with the declared B105 targets; on the full lane the same `r2` (`verify.py:1254`) and r3/canary targets of `src/assay/cli.py` (`verify.py:809-823`); `verify_document == []` still holds.
      - Negatives: an analysis target (A-478 in the message); a missing `vocabulary.py`; `source_roots ["src"]`.
      - Hermetic, with a tmp repo holding a copy of the checker: a committed `src/extra/x.py` is unclassified, while an **untracked** one changes nothing; a missing analysis `__init__.py` is stale; a committed `src/assay/new.py` missing from the r1 targets is refused, naming it.
    - **T10 `test_distribution_gate.py`.** `analysis-lane-passed` is required and comes after `run_self_hosted_lane`. The lint scope and the fake clone include `analysis`. A planted finding there makes lint red.
11. **Docs.**
    - README :26-72: one wheel and zipapp; the package loads only for `analyze`; B105 covers only `src/assay`; analysis has its own R0+R1 lane; a source checkout needs `analysis/src` on the path for `analyze`.
    - DESIGN-GUIDE: `### Package boundary (A-478)` under :3406: analysis reads evidence and never judges (317 of 3,760 B105 candidates); the seam, private-name rule, checker, no R2.
    - CONSUMERS :4021: `import assay.analysis` is gone. CHANGES: an entry under Changed.

## Oracles
Record each controlled break in the LOG, red then green.

| # | Observable | Negative |
|---|---|---|
| O1 CLI unchanged | T1 passes with only the step-10 edits and the FIFO change; the LOG shows each `--help` and `collect` output byte-identical before and after | Removing the `_run_analyze` call fails T1 |
| O2 Seam lazy and one-way; boundaries; A-005 | T3, T5, T6, T7 | An eager import, the `raw[:1] !=` mutant, or tainted sources fail them |
| O3 One wheel and zipapp | T7, T8 | Without `analysis/src` in `where`, T8 fails |
| O4 Own lane and rigor | T3; the gate prints `analysis-lane-passed` | Delete one branch-covering test (locally): coverage drops below 100% |
| O5 B105 scope named | Step 8, T9, T10 | The T9 and T10 negatives |
| O6 Judge floor holds without the analysis tests | Preflight passes; checker ACCEPTED | A line only analysis tests covered (e.g. `git.ignore_rule_source`) turns R1 red. Fix it with a judge test in the W3-placed file of that module (e.g. `tests/core/test_b105_git_process_boundaries.py` for `git.py`; counts toward the 2-file allowance), never a pragma. Use the preflight to find real gaps, do not predict them |

**§3b (pasted). An oracle must not contain any of the following.**
A (L20) no assert after a deadline/`sleep`, or on elapsed time or iteration counts (wait on join/Event/queue; never widen a timeout). B no unrestored global, no monkeypatch on a `__getattr__` proxy (L19), fresh `tmp_path`. C no `pass` body, "nothing raised" test, trivia, or weakened assertion. D (L11) no no-cover pragma. E no real network, no `now()` in an asserted value. F no predicted coverage or mutation numbers. Check: could it flip on a slower host, another worker or another order?

## Scope
- **Touch:** `analysis/**`, `src/assay/analysis.py`, step 4 of `cli.py`, `pyproject.toml`, `assay.toml`, the 2 `tools/` files, T1-T10 (T6 is in W3's `tests/core/test_import_contracts.py`), the 4 docs, B127 status, `nyxloom-trove/reports/wave-a/W2-LOG.md`.
- **Forbid:** other judge modules; moving judge helpers; a shim; moving schemas (CD16); R2 (CD17); `build_release.py` (probed, not needed); `run-gate.toml`, `nyxloom.toml`, `self-qualification-gate.sh`.
- Needing more than 2 files outside this list triggers BLOCKED.

## Gate
1. Run serially: `nice -n 19 ionice -c3 python -m pytest analysis/tests <T4-T10 files> -q -p no:cacheprovider`.
2. `cd <worktree>/assay && python ./run-gate.py tester-unified`. Then, **in a separate step**, read `ASSAY_GATE_PHASE=analysis-lane-passed`, `ASSAY_GATE_CONTAINER_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE`. Never read them through a pipe tail.
3. `python ./run-gate.py self-qualification-preflight` (O6), which plan §4 allows.

**No known red (CD29).** Main's `35adca38` (merged into landing at `1e3c8a49`) deleted that test. Any gate failure is red.

**Host-load rule:** shared production host; everything under nice/ionice; at most one gate container and none while another session's gate runs (`docker ps`); never the full `self-qualification` lane; remove containers by exact name only.

Use editor tools only, and keep the docs in sync. Trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

## BLOCKED rule
If a named contract cannot be met, or the work needs a forbidden file: STOP. Write `BLOCKED: <reason>` to `W2-LOG.md`, commit, and exit. Do not improvise; BLOCKED counts as success. Triggers:
- `verify_document` refuses T9 for a reason other than scope;
- 100% R1 needs a pragma or a change in judge behavior;
- the wheel ships tests, or the egg-info leaves `src/`;
- W3 contradicts step 1.

## Review
A fresh-session adversarial review, never a fork, happens before landing. It adds one combined-axis attack (for example, the zipapp running `analyze record -- cmd --flag`).

## Former carver questions
- **Q1 schemas.** Decided (CD16): they stay in `src/assay/schemas/`; no documented path changes.
- **Q2 analysis R2.** Decided (CD17): R0+R1 whole-target lane only; R2 for analysis is B131.
- **Q3 private judge names.** Decided (CD18): the allowlist stays empty; anything analysis needs becomes a public judge name (`PROGRESS_EVENTS` is already public via `mutation.__all__`).
- **Q4 `import assay.analysis`.** Decided (CD19): dropping it is acceptable; 7.2.0 stands.
