# Pre-dispatch adversarial review: Wave A code briefs (W2, REBASE-P8, W10, REBASE-P0-P2)

**Reviewer:** fresh session (Claude Opus 5.5). This is not a fork, and I wrote none of the reviewed text.
**Worktree:** `.worktrees/assay-b110-landing`, HEAD `be803c3a`. Paths below are relative to `assay/`.
**Method:** AUTHORING §"Pre-dispatch adversarial handoff review" and §3b.
**What I ran:** read-only probes only, under nice/ionice:
- AST scans;
- `idioms.py` into `scratchpad/rv2/`;
- `PYTHONPATH=src python -c` against the analyze CLI, with no writes;
- `python -m assay --help`.

**Baseline fact used throughout.** `5bbd916e..be803c3a` changes no `src/` file. The only changes are:
- `tests/fixtures/b105-coverage-exclusions.json` (liveness pinned at `[94, 95]`, commit `55611561`);
- `tests/test_cli_run.py` (−216 lines: main's `35adca38`, merged at `1e3c8a49`).

Every `src/` anchor "at 5bbd916e" is therefore also an anchor at HEAD.

Severity scale:
- **BLOCKING:** the Sonnet implementer will hit a red gate or a contradiction that it cannot resolve without a design choice.
- **MAJOR:** an open design choice, or an unsound oracle.
- **MINOR:** accuracy or clarity.

---

## 0. Anchor verification (≥25 required; 150+ checked)

| Doc | Anchors checked at HEAD | Result |
|---|---|---|
| W2 | See the list below the table. | All correct except `evaluate.py:1256`: the TARGET_NOT_MEASURED raise is at 1262-1270, with the reason at 1269 (MINOR). |
| W10 | Decorator counts, the positional-construction lines, every I2, I3, I4 and verify-side site, the exclusion lines, and the DESIGN-GUIDE anchors. See the list below the table. | All sites match their rows textually. Semantic problems are listed in §3. |
| REBASE-P8 | `mutation.py`, `runner.py`, `cli.py` and docs anchors. See the list below the table. | Correct, within ±3 lines. |
| REBASE-P0-P2 | All P2 "unchanged" anchors and all P0 "moved" anchors. See the list below the table. | Correct, except the stale items in F-R0-6 and F-R0-7. |

**W2 anchors checked:**
- `cli.py` :104-134, :134, :220, :470 and :485-486;
- `analysis.py` :704 and :1023-1130;
- `assay.toml` :124, :235 and :186-282;
- `config.py` :2247-2266;
- `test_self_lane.py` :40, :88 and :128-135;
- `test_analysis.py` :58, :993, :1006, :1062 and :1061-1084;
- `test_cli_lanes.py` :36-49;
- `verify.py` :1254 and :809-823;
- gate script :134-152 and :302-362;
- README :26-72, DESIGN-GUIDE :3406, CONSUMERS :4021 and :4152.

**W10 anchors checked:**
- **Decorator counts.** The claim of 68 `@dataclass(frozen=True, kw_only=True)` and 12 `@dataclass(frozen=True)` holds. All are at module level, and none has a non-decorator use. 68×2 + 12×1 = 148, and `records.py` carries 3 flags in `dataclass(...)` calls plus 3 in `dataclass_transform(...)` calls = 6.
- **Positional construction lines:** `python.py:767`, `sql.py:756`, and `test_b105_config_boundaries.py` :65, :69, :72, :76, :88, :308 and :309.
- **I2 call sites (all 88):**
  - row 1: 23;
  - row 2: 26;
  - row 3: 10;
  - row 4: 5;
  - row 5: 7;
  - row 6: 2;
  - sha256: 7;
  - aware: 2;
  - percentage: 5.
- **Verify-side `_is_int` and `_is_text` sites:** 12.
- **I3 sites:**
  - `verdict.py` at 4769, 4770, 4799, 4807, 4865, 4866, 4910, 4911, 4990, 4991, 5019 and 5020;
  - `runner.py` at 2176-2177, 4653 and 4655;
  - `verify.py` at 684, 712, 775, 915, 1043, 1527 and 1625, and at 2565, 2637, 2685, 2703, 2812 and 2959;
  - the policy blocks at 4785-4797, 4813-4825, 4868-4879, 4913-4924, 700-709, 726-735 and 781-788.
- **I4 sites:** E1–E10.
- **Exclusion lines:** config 94, liveness 94, mutation 148, mutation_witness 14, and git 376 and 1342.
- **DESIGN-GUIDE:** :1972 and :1459.

**REBASE-P8 anchors checked:**
- **`mutation.py`:**
  - 206, 846, 998, 1023, 1273, 1515, 1644, 1696 and 1746;
  - `judge` at 2602-2615;
  - `resume` at 2668-2680;
  - `candidates` at 2693-2701;
  - the `candidate` event at 3191;
  - evidence at 3205 and 3244;
  - rejudge at 2403-2418 and 2635-2660.
- **`runner.py`:** 3097, 3751 and 6350.
- **`cli.py`:** 1571, 1648-1765 and 1820-1828.
- **Docs:** CONSUMERS 4021 and 4030, DESIGN-GUIDE 3406.

**REBASE-P0-P2 anchors checked:**
- **P2 "unchanged" anchors:**
  - go 203, 326, 334 and 356;
  - javascript 176 and 258;
  - sql_lex 64, 197 and 280;
  - go_modfile 77 and 393;
  - isolation 52 and 1406;
  - git 261, 335, 357, 376 and 1342;
  - cli 488;
  - python 881.
- **P0 "moved" anchors in `liveness.py`:**
  - 544, 549, 560-561, 606 and 627-642;
  - 1036, 1080, 1340, 1381, 1394-1402, 1418 and 1484;
  - 1643-1644, 1671-1677 and 1692.
- **P0 "moved" anchors in `mutation.py`:**
  - 1890 and 1913;
  - 2141;
  - 2474 and 2518-2521;
  - 2895, 2906 and 2991-2998;
  - 3107 and 3179-3251.
- **Other P0 anchors:**
  - runner 3097 and 4427;
  - `test_liveness_runner_monitor.py` 119-150 and 1069;
  - config 1063;
  - DESIGN-GUIDE 484 and README 975.

---

## 1. W2 — `W2-analysis-package.md`

**F-W2-1 BLOCKING: the moved FIFO test loses its fixture, and CD13's remedy cannot work as written.**

*Location:* W2 step 1 (move `tests/test_analysis*.py`), step 10 T1 and "Add no `conftest.py`", together with CD13.

*Evidence:*
- `tests/test_analysis.py:721` `test_nonregular_inputs_refuse_without_waiting_for_a_fifo_writer(tmp_path, kind, standalone)` uses the `standalone` fixture from `tests/conftest.py`. My AST scan confirms it is the only conftest fixture either analysis test file uses.
- In `analysis/tests/` without a conftest, the fixture is not found. The test errors, and the new analysis lane is red.
- W2 never mentions this test.
- CD13 says it "runs assay from source (`sys.executable -m assay` with `src` on the path)", but there is no `src/assay/__main__.py`. I probed it: `PYTHONPATH=src python -m assay --help` gives "No module named assay.__main__".
- CD13 also omits `analysis/src`, without which `analyze` raises `ModuleNotFoundError`.
- The test passes `-I`, which ignores `PYTHONPATH`.

*Fix text (append to T1):*

> **FIFO test (CD13, corrected).** In `analysis/tests/analysis_support.py` add:
> ```python
> SOURCE_ASSAY_MAIN = (
>     "import sys; "
>     f"sys.path[:0] = [{str(PROJECT_ROOT / 'src')!r}, {str(PROJECT_ROOT / 'analysis' / 'src')!r}]; "
>     "from assay.cli import main; sys.exit(main(sys.argv[1:]))"
> )
> ```
> In `test_nonregular_inputs_refuse_without_waiting_for_a_fifo_writer`:
> - drop the `standalone` parameter;
> - replace the `subprocess.run` argv with `[sys.executable, "-I", "-c", SOURCE_ASSAY_MAIN, "analyze", *arguments]`;
> - keep every other argument (`capture_output=True, text=True, timeout=10, check=False`) and both assertions unchanged.
>
> CD13's `-m assay` does not exist: there is no `assay/__main__.py`.

Also correct CD13 and W4 C5 to match. W4 C5 has the same `python -m assay analyze` error.

**F-W2-2 BLOCKING: W3's import-contract file must be edited, but W2 does not say how.**

*Location:* step 10 T6 ("It goes in W3's import-contract test, or else in `test_dependency_purity.py`"), plus W3 2a.

*Evidence:*
- W3 creates `tests/core/test_import_contracts.py` with a row `if m == "assay.analysis": return "analysis"   # W2 removes this row`, `ANALYSIS_DEPS`, a `ci == "analysis"` branch, and a refusal case `assay.verdict->assay.analysis`.
- W2 names none of these edits. "Or else" is also an open placement choice.
- If Sonnet removes the row, as W3 expects, the `assay.verdict->assay.analysis` refusal flips to allowed (core→core), and W3's test (ii) goes red.

*Fix text (replace T6):*

> **T6 in `tests/core/test_import_contracts.py`** (W3's file; add it to the Touch list):
> 1. Delete the line `if m == "assay.analysis": return "analysis"`, delete `ANALYSIS_DEPS`, delete the line `if ci == "analysis": return imported in ANALYSIS_DEPS`, and change `if ci == ct and ci != "analysis": return True` to `if ci == ct: return True`.
> 2. In test (ii), replace the refused case `assay.verdict->assay.analysis` with `assay.verdict->assay.cli`. Core must not import the composition root.
> 3. Add `test_only_cli_run_analyze_imports_assay_analysis`. It uses `ast.walk` over every `src/assay/**/*.py` and collects each `Import` or `ImportFrom` whose module is `assay_analysis` or starts with `assay_analysis.`. It asserts exactly one hit, in `src/assay/cli.py` inside the `FunctionDef` `_run_analyze`. Its tainted negative feeds the same checker a synthetic module-level `import assay_analysis.cli` and expects a refusal.

**F-W2-3 BLOCKING: `evidence.py` keeps an unused import, which turns the pyflakes phase red.**

*Evidence:*
- In `analysis.py`, `AssayError` is used only at :1126 (in `cmd_analyze`).
- After the split, `from assay.errors import AssayError` in `evidence.py` is unused. My AST scan confirms every other import stays used.
- Step 2 says "Nothing else changes", and step 9 lints `analysis/**`.

*Fix text (step 2):* "Delete the `AssayError` import from `evidence.py`: only `cmd_analyze` used it. Keep every other import."

*Fix text (step 3), the imports exactly:*

> `from __future__ import annotations`, `import argparse`, `import json`, `import subprocess`, `from pathlib import Path`, `from typing import TextIO`, `from assay.cli import AssayArgumentParser`, `from assay.errors import AssayError`, `from assay_analysis import evidence`.
>
> `cmd_analyze` reaches the private evidence helpers as `evidence._report_commit`, `evidence._report_max_errors`, `evidence._tester_run`, `evidence._report_text`, `evidence._output_location` and `evidence._write_new`.

**F-W2-4 MAJOR: the docs test needs a third edit, and O1 contradicts step 10.**

*Evidence:*
- `test_analysis.py:1080` does `subcommands = parser._subparsers._group_actions[0].choices["analyze"]._subparsers._group_actions[0].choices`. On `assay_analysis.cli.build_parser()`, which has no `analyze` level, this raises `KeyError`.
- O1 says "T1 passes unedited", yet step 10 edits T1.

*Fix text (T1 docs test):* "Also change :1080 to `subcommands = parser._subparsers._group_actions[0].choices`."

*Fix text (O1):* "T1 passes with only the step-10 edits and the F-W2-1 FIFO change."

**F-W2-5 MAJOR: CD15's pin disagrees with W4, and nothing decides `testpaths`.**

*Evidence:*
- W4 Work 3 still pins "ini keys exactly `pythonpath=["src"]`, `testpaths=["tests"]`".
- W4's P3 pyproject-comment row says `pythonpath = ["src"]` is pinned.
- CD15 overrides only `pythonpath`.
- W2 step 5 sets `testpaths = ["tests", "analysis/tests"]`, so W4's pin goes red on W2's pyproject.

*Fix text (CD15, and W4 Work 3 plus its P3 row):* "The drift test pins exactly `pythonpath = ["src", "analysis/src"]` and `testpaths = ["tests", "analysis/tests"]`. `gate/tests` stays out of `testpaths`, because lanes name it explicitly."

**F-W2-6 MAJOR: the gate function is elided ("same shape", `assay verify ...`).**

*Fix text (step 9, verbatim):*
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
  assay verify "$scratch/verdict-analysis.json" \
    || die 'assay verify refused the analysis lane verdict'
  echo 'ASSAY_GATE_PHASE=analysis-lane-passed'
}
```
- Call it on the line directly after `run_self_hosted_lane "$worktree" "$scratch" "$version" "$wheel"`. PATH is already exported there.
- Do not call `require_emitted_*` for this verdict: `--require-judge-provenance` binds it.

**F-W2-7 MAJOR: the T3 AST rule is under-specified, and a plausible wrong implementation passes it.**

*Evidence:* T3's only negative is `from assay.mutation import _x`. A scan of `ImportFrom` names alone passes that negative and still misses:
- `from assay import cli as c; c._x`;
- `import assay.cli; assay.cli._x`;
- `evidence.git._x`, a judge module re-exported through `evidence`;
- `getattr(runner, "_x")`.

The WIP draft already uses the alias form (`from . import cli as assay_cli` … `assay_cli._resolve_declared_adapters`).

*Fix text (T3):*

> Scan every `analysis/src/**/*.py`.
> 1. Bind every name introduced by `import assay[.x] [as y]`, by `from assay import x [as y]` where `x` is a module, and by `from assay[.x] import y` where `y` is a module, and track each name as a judge module.
> 2. Flag every `ImportFrom` from `assay[.*]` that imports a name matching `^_[^_]`.
> 3. Flag every `Attribute` whose `attr` matches `^_[^_]` and whose value resolves to a judge module. This includes a chain through `evidence.<judge module>`.
> 4. Flag every `getattr(<judge module>, "<_name>")`.
> 5. Private access is allowed only on `self`, `cls`, and the sibling analysis modules (`evidence`, `cli`, `campaign`).
>
> Add negatives for the alias form, the dotted form and the re-export chain.

**F-W2-8 MAJOR: T9 does not pin how the checker reads the tree, and needs a negative for it.**

*Evidence:* (b) and (e) say "tracked" and `git ls-tree -r --name-only`, but give no revision, prefix stripping or trailing slash. An implementation that `rglob`s the working tree passes every T9 case listed.

*Fix text (step 7):*

> (b) uses `git -C <repo_root> ls-tree --name-only <expected_commit> -- <prefix>/src/`, with the trailing slash, and takes basenames. (e) uses `git -C <repo_root> ls-tree -r --name-only <expected_commit> -- <prefix>/src/assay/`, keeps the `*.py` names, and strips `<prefix>/` when `prefix != "."`.
>
> T9 hermetic negatives: an **untracked** `src/extra/x.py` does not change the result, while a committed one is unclassified. A committed `src/assay/new.py` that is missing from r1 targets is refused, with its name in the message.

**F-W2-9 MINOR: the private-name claim, and where the new tests go.**
- `PROGRESS_EVENTS` has no underscore and is in `mutation.__all__` (`mutation.py:171`), so CD18's empty allowlist holds **for W2**. Keep the context line.
- *Fix text (T4 and T5 paths):* T5 is `tests/core/test_cli_analyze_seam.py`, and T4 edits `tests/core/test_cli_lanes.py`, per W3's `expected_dir`.

**F-W2-10 MINOR: the fix for O6 has no home.**

*Fix text (O6):*

> A `src/assay` line covered only by analysis tests gets its covering test in the W3-placed judge test file of that module, for example `tests/core/test_b105_git_process_boundaries.py` for `git.py`. Such files count toward the 2-file allowance.
>
> `git.ignore_rule_source` already has judge tests (`test_b105_git_process_boundaries.py:285-349`), so use the preflight to find real gaps. Do not predict them.

**F-W2-11 MINOR: W1 and W2 are left to merge in either order.**

*Evidence:* W2 inserts `run_analysis_lane` right after `run_self_hosted_lane`. That is exactly where W1 deletes the Topos and CMRU phases (`tester-unified-gate.sh:659-700`).

*Fix text:* "W2 is cut after W1 merges. If W1 is still open, W2 waits."

**F-W2-12 MINOR: the "Carver questions" section is stale.**

*Fix text:* replace the section with "Decided (CD16–CD19): schemas stay; analysis R2 is B131; the private-name allowlist stays empty (public names instead); dropping `import assay.analysis` keeps 7.2.0."

**F-W2-13 MINOR: the obsolete known-red note (item 8).**

*Evidence:* Line 147 relies on the plan §4 known red. `35adca38` is in HEAD (merged at `1e3c8a49`), so the test no longer exists.

*Fix text:* "No known red: main's `35adca38` (merged into landing at `1e3c8a49`) deleted that test. Any gate failure is red."

**Q5 answers (claim safety).**
- **Named scope rule:** yes. The rule is carried by `OUT_OF_SCOPE_BY_DECISION` plus A-478 in each refusal and the ACCEPTED suffix. One improvement, MINOR: the checker does not verify that the out-of-scope package is *compensated*, i.e. that `[lanes.analysis]` exists. Optional (f): read `assay.toml` at `expected_commit` and require `[lanes.analysis]` with `rigor == ["R0","R1"]` and targets equal to the tracked `*.py` under `analysis/src/assay_analysis/`.
- **Zipapp:** yes, `assay analyze` keeps working. `build_zipapp` pip-installs the wheel `--target` into its staging area (`build_release.py:370-399`). Both top-level packages land at the archive root, and `from assay_analysis.cli import main` resolves through zipimport. T8 proves it.
- **Pythonpath pin:** no. It does not yet agree with W4 (F-W2-5).
- **Consumer-visible paths:** no consumer-visible break.
  - Schemas stay (CD16).
  - `git grep` over vbpub (excluding assay) and grep over dstdns find no `assay.analysis` import (CD19 holds).
  - The one source-path consumer, `libraries/cli-extended/run-gate.toml:24` (`PYTHONPATH=…/assay/src python -m assay.cli run …`), uses `run` only and never loads the analysis package.
  - The new top-level `assay_analysis` package is additive.
  - Add one docs line: "a source checkout needs `analysis/src` on the path for `analyze`".
- **`PROGRESS_EVENTS`:** it is public (see F-W2-9).

**Plausible wrong implementations that still pass W2's oracles:**
- the alias-form private access (F-W2-7);
- a working-tree scope scan (F-W2-8);
- an eager top-level `from assay_analysis.cli import main`. T5(ii) passes it, because `assay.cli` is already imported before the monkeypatch, so only T6 catches it. State in T5 that the laziness proof is T6.

---

## 2. REBASE-P8 — `REBASE-P8.md` + `b110/P8-campaign-analysis-core.md` + the preserved draft

**F-P8-1 BLOCKING: §4's private-name default contradicts CD18.**

*Evidence:*
- §4 says: "Default: add each name to `ALLOWED_PRIVATE_JUDGE_NAMES`".
- CD18 says: "The allowlist mechanism exists but stays empty. Anything analysis needs becomes a public judge name."
- `runner.py` is not in P8's Touch list.

*Fix text (replace §4):*

> Add public aliases (CD18). The allowlist stays empty. Each alias is one line directly after its function:
> - `cli.py`: `resolve_declared_adapters = _resolve_declared_adapters`;
> - `runner.py`: `resolve_declared_base = _resolve_declared_base`;
> - `mutation.py`: `execution_from_state_record = _execution_from_state_record` and `valid_hung_resource_evidence = _valid_hung_resource_evidence`.
>
> Add each to its module's `__all__` in sorted position. Add `runner.py` (the alias line only) to the Touch list. `campaign.py` uses only the public names. `plan_jobs` replaces `_cmd_plan`.

**F-P8-2 BLOCKING: the §6 Q-P8b default contradicts CD20.**

*Evidence:* CD20 says "Hung rows show B107's liveness decision and evidence status, read-only." §6 says "Default: not added."

*Fix text (replace Q-P8b):*

> Every candidate row, in both `candidate_details` and the `adverse` lists, carries two new keys:
> - `liveness_decision`: a string or `null`. It is the verbatim `liveness_resource_evidence["decision"]` when that value is a string. The source is the latest-run `candidate` event, else the counted state record, else `null`. It is `null` for rows whose outcome is not `hung`.
> - `liveness_evidence_status`: `"valid"`, `"invalid"`, `"absent"` or `null`. For `hung` rows it is `"absent"` when no evidence object exists, and otherwise the result of `mutation.valid_hung_resource_evidence(...)` mapped to `"valid"` or `"invalid"`. It is `null` for rows that are not `hung`.
>
> The schema declares both keys, with `additionalProperties: false` kept.

**F-P8-3 BLOCKING: the C29 flip moves P6's open degree of freedom onto a Sonnet implementer.**

*Evidence:*
- P6 left the signature of `_discover_plan_jobs` free (P6 :354-355, "Give it a stable signature").
- P8 only says "`_plan_rows_from_discovery(discovered)` over P6's output".
- The discovery block reads `args.operators`, `args.shard`, `args.reuse_from`, `args.request_base` and `args.allow_dirty` (`cli.py:1582-1653`).
- P6's `campaign init` needs the **full pre-shard** list for its digest (C23).

*Fix text (append to §1):*

> ```python
> @dataclass(frozen=True, kw_only=True)
> class _PlanDiscovery:
>     jobs: "tuple[mutation.MutantJob, ...] | str"   # mutation.UNSUPPORTED passes through unchanged
>     worktree_integrity: "WorktreeIntegrity | None"
>     reuse_source: Any
>     reuse_command_plan: Any
>     reuse_command_cwd: "Path | None"
>     project_prefix: str
> def _discover_plan_jobs(lane_file: LaneFile, lane: Lane, *, operators: tuple[str, ...],
>                         request_base: str | None, allow_dirty: bool, reuse_source: Any) -> _PlanDiscovery:
> ```
> - The body is `cli.py:1648-1763` verbatim: from `deadline = …` through `collect_mutation_sites(...)`.
> - `_cmd_plan` keeps its argument validation (:1578-1646) and the rest from `if jobs == mutation.UNSUPPORTED:` (:1765 onward) unchanged, reading its values from the returned `_PlanDiscovery`.
> - `jobs` is the **full** discovered list, **before** shard selection.
> - Add a docstring: "Single planner-jobs extraction (C29); P6 reuses it."
> - Add a judge test: for a lane with `--shard 0/2`, `_discover_plan_jobs(...).jobs` has the unsharded length.
>
> `PlanRow` is a `typing.TypedDict` (not a dataclass) with exactly the 9 keys.
> `plan_jobs(lane_file, lane, *, request_base=None, allow_dirty=False)` calls `_discover_plan_jobs(..., operators=lane.judge.mutation.operators, reuse_source=None)`.

**F-P8-4 MAJOR: the port steps for the preserved draft are incomplete.**

The draft is compatible in substance with the package layout: the signatures of `runner._resolve_declared_base` and `evaluate_r1` are unchanged since `30eec294`. But §2 lists only `_json`, `_report_commit` and `_identity`.

*Fix text (add §2a "Port edits, exactly"):*

> 1. Change the imports:
>    - `from . import analysis, git` becomes `from assay import git` plus `from assay_analysis import evidence`;
>    - every `analysis.X` becomes `evidence.X`, including `analysis.verify_text` (campaign.py:112);
>    - `from . import coverage as coverage_api` becomes `from assay import coverage as coverage_api`;
>    - `.config`, `.errors`, `.mutation`, `.safeio` and `.verdict` become `assay.<same>`;
>    - the function-level `from . import cli as assay_cli[, runner]` becomes `from assay import cli as assay_cli[, runner]`.
> 2. Delete `from statistics import median` (campaign.py:22 and its uses at :1101 and :1116). P8's ETA uses nearest rank.
> 3. `_lane_plan` (:231-268) calls `assay_cli.plan_jobs(...)`. Delete the hand-built `Namespace` and the `_cmd_plan` call. Keep the `_resolved_base` comparison through `runner.resolve_declared_base`.
> 4. Change the hard-coded modes (:1032-1035) to generic counting (O14).
> 5. The hook lives in `assay_analysis/cli.py`:
>    - `build_parser()` calls `campaign.build_campaign_parser(commands)` right after `commands` is created;
>    - `cmd_analyze` starts with `if args.analysis_command == "campaign": return campaign.run_campaign_command(args, stdout=stdout, stderr=stderr)`.
> 6. The test module imports `from assay_analysis import campaign as campaign_api`. Its `Path(__file__).parent / "fixtures" / "verdicts"` becomes `JUDGE_VERDICT_FIXTURES`.
> 7. **The docs test.** `test_documented_analysis_commands_parse_with_shipped_cli` requires every subcommand to appear in an `<!-- assay-analysis-example -->` bash block. Put the README `campaign` example inside the existing block at README :52-58. It must parse with all required arguments: `lane`, `--file`, `--expected-commit` and `--progress`.
> 8. Add `analysis/src/assay_analysis/campaign.py` to `[lanes.analysis].judge.targets`, sorted.
> 9. Add `state.unverified_hung` to the schema's `state` object.

**F-P8-5 MAJOR: the pilot flag is left ambiguous.**

*Fix text (§1, "Step 9 is deferred"):*

> Do **not** add `--candidates-file` or `--project-jobs`'s pilot semantics. argparse rejects `--candidates-file` (exit 2). `--project --project-jobs N` stays in Wave A (step 8). Every **non-`evidence_error`** document carries `qualifying: true`. The `evidence_error` document keeps its closed 7-key shape. The schema rule `complete ⇒ qualifying true` stays.

**F-P8-6 MAJOR: O16 is split wrongly across the judge and analysis trees.**

*Evidence:* O16's second half, "a tampered `source_sha256` makes a matching state record an `evidence_error`", runs the campaign analysis. Under A-476, `tests/` tests only `src/assay`.

*Fix text:*

> O16a in `tests/core/test_cli_plan_jobs.py` covers:
> - the rows' identity invariant;
> - the `"UNSUPPORTED"` branch;
> - the unsharded-length test from F-P8-3.
>
> O20 goes in `tests/core/test_mutation_candidates_event_judge.py`. O16b (tamper → `evidence_error`) goes in `analysis/tests/test_analysis_campaign.py`. Both `plan_jobs` branches must be covered by judge tests: B105 no longer sees analysis tests.

**F-P8-7 MAJOR: the §6 negative cannot detect a copied predicate (plausible wrong implementation).**

*Evidence:* A copy of `_valid_hung_resource_evidence` inside `campaign.py` passes the §6 fixture.

*Fix text (add the negative):*

> With `monkeypatch.setattr(assay.mutation, "valid_hung_resource_evidence", lambda value: True)`, the pre-B107 record is counted and `unverified_hung.count == 0`. This proves the judge predicate is called. Also add the reverse: a hung record with valid B107 evidence is counted and is not in `unverified_hung`.

**F-P8-8 MAJOR: CD20 says P6's brief is annotated, but at HEAD it is not.**

*Evidence:* `P6-campaign-deadline.md` :354-355 and :381 still say P6 extracts.

*Fix text (carver, before W9 dispatch):*
- Change P6 step 8 to: "`_discover_plan_jobs` already exists (W9/P8, C29 flipped). Reuse it and do not re-extract. BLOCKED if it is absent."
- Delete P6 DoF :354-355.
- Annotate P9 :9 and :91: `campaign.py` now lives at `analysis/src/assay_analysis/campaign.py`.

**F-P8-9 MINOR: the stale P8 header items that the note must override explicitly.**

The note must override each of these by name:
- Implementer is Opus;
- the branch is the old one;
- "Depends on P6 merged";
- Work step 2's R2 budget note;
- Work step 10 C15;
- Scope still lists `src/assay/analysis.py`, `src/assay/campaign.py` and `tests/test_campaign.py`;
- Gate step 1's file list.

Recommendation: fold the rebase note into one self-contained `wave-a/W9-campaign.md`. A Sonnet implementer should not read a 678-line brief plus a delta note that overrides about 25 of its statements.

**Q6 answers.**
- **Anchor deltas:** correct.
- **Conflict with P6:** yes, until F-P8-3 fixes the signature and F-P8-8 annotates P6. The pre-shard list and the operators override are what P6's `campaign init` needs.
- **Draft compatibility:** compatible in substance. The port steps are missing (F-P8-4).

---

## 3. W10 — `W10-dry.md`

**F-W10-1 BLOCKING (A-182): `candidate_identity.py` sits on the verify boundary.**

*Evidence:*
- `verify.py:93` imports `candidate_id_from_fields`, and the raw layer calls it at `verify.py:1715`.
- The module docstring reads: "Keep this module dependency-free: planning, execution, verdict construction and the independent verifier all use this one canonical byte sequence."
- Rows 1 and 7 rewrite `candidate_identity` 22, 24 and 31 onto `guards`. The raw verifier would then execute a producer helper module, and one mutant in `is_nonempty_str` would disable both the model's path check (verdict 795) and the raw identity check at once.

*Fix text:*
- Delete `candidate_identity 22,24` from row 1 and `candidate_identity 31` from the sha256 row.
- Add to Rejected: "`candidate_identity.py` (all sites): imported by `verify.py`, documented dependency-free; routing it through `guards` makes the raw verifier share a producer helper (A-182)."
- O5 adds: "`candidate_identity.py` imports no `assay` module", with a tainted negative.

**F-W10-2 BLOCKING: W3's contract refuses the new edges, and W10's own trigger then fires.**

*Evidence:*
- W3's `ADAPTER_DEPS` is `{adapters.base, errors, mutation, safeio, statement_attribution}` and `PARSER_DEPS` is `{errors, vocabulary}`.
- I1 needs `records` in `adapters/{go,go_modfile,javascript,python,sql,sql_lex}.py` and in `{coverage_parsers,mutation_parsers,result_reports}/model.py`.
- I2 needs `guards` in `go_stmtpos`, `coverage_istanbul_json`, `coverage_py_json`, `mutation_report_json` and `vitest_json`.
- The brief says "If W3's contract lists core modules explicitly, add both" (it does not list them), and its BLOCKED trigger is "W3's contract refuses a core import".

*Fix text:*
- Replace with: "In `tests/core/test_import_contracts.py`, add `"assay.records"` and `"assay.guards"` to **both** `ADAPTER_DEPS` and `PARSER_DEPS`. That is the only edit to that file."
- Change the trigger to: "W3's contract refuses an edge other than an adapter or parser module → `assay.records`/`assay.guards`."

**F-W10-3 BLOCKING: E5's signature cannot be implemented.**

*Evidence:* `_emit_verdict_written` is a closure nested in `_run_reserved` (`cli.py:1206`). A file-level `_deliver_verdict(verdict, destination, args, out)` cannot call it.

*Fix text (E5 row):*

> `_deliver_verdict(verdict: Verdict) -> int`, a **nested** function in `_run_reserved`, defined directly after `_emit_verdict_written`.
> - It closes over `destination`, `args` and `out`, none of which is rebound in `_run_reserved`.
> - Its body is `cli.py:1545-1553` verbatim, including the A-181 comment.
> - The three sites become `return _deliver_verdict(verdict)`.

**F-W10-4 BLOCKING: step 1's contract test breaks at step 2, which O3 forbids.**

*Evidence:*
- P1's `_declared()` finds classes through `_is_dataclass_decorator`, i.e. a Name or Attribute `dataclass`. After I1, `reflected == declared` in `_observe()` fails, because the AST finds no dataclass.
- O3 says "the step 1 tests are unchanged after step 1".

*Fix text (step 1):*

> Write `_is_dataclass_decorator` to recognize a Name or Attribute `dataclass`, `record` or `positional_record`, or a `Call` of any of them. The step-1 functions then pass before and after I1 without edits. Step 2's bypass and params rules are **new** test functions.

**F-W10-5 MAJOR: O5 misses a re-export (item 4).**

*Evidence:* O5 compares only the *module* set that `verify.py` imports. Once `verdict.py` does `from .guards import is_strict_int`, `from .verdict import is_strict_int` or `claim_for` in `verify.py` keeps the module set identical and passes.

*Fix text (O5):*

> Obs:
> - (a) the exact set of `(module, name)` pairs that `verify.py` imports equals the committed base;
> - (b) at runtime, no value in `vars(assay.verify)` `is` any function in `vars(assay.guards)` or `vars(assay.records)`, or `assay.verdict.claim_for`, `claim_carries` or `_require_policy_iff_attempted`;
> - (c) no module under `src/assay` other than `verify.py` imports a `_`-prefixed name from `verify`. Tests are exempt: `test_verify_snapshot_policy.py:17` imports `_check_snapshot_policy`.
>
> Negatives, run as synthetic sources:
> - `from .verdict import is_strict_int` inside `verify`;
> - `verdict.claim_for(...)` used inside `verify`.

**F-W10-6 MAJOR: rewrites leave unused imports, which turn the lint phase red.**

*Evidence:*
- `verdict.py` uses `math.` only at 1937 and 2926, both of which become `is_finite_positive`.
- `isolation.py` uses `math.` in code only at 1125. Lines 1117 and 1129 are a comment and a string.

*Fix text:* "Delete every import a rewrite leaves unused. At `be803c3a` that means `import math` in `verdict.py` and in `isolation.py`. Before each commit, run the gate's pyflakes over `src/assay` (lint wheelhouse), or check it by AST."

**F-W10-7 MAJOR: the helper packet overstates what the guards do and omits their imports.**

- **The claim that no helper raises is wrong.** "None of these raises" is false for `is_finite_positive(10**400)` and `is_positive_or_inf(10**400)`, whose `math.isfinite` raises `OverflowError`. The inline code does the same, so behaviour stays identical, but a Sonnet told "never raises" may add a `try`. *Fix text:* "…raises only what the inline code raised: `OverflowError` for ints beyond float range. Never catch it. O1 adds `10**400` → `OverflowError` for both."
- **The imports are missing.** The "exact" `guards.py` omits them. *Fix text:* prepend `from __future__ import annotations`, `import math` and `from datetime import datetime`.

**F-W10-8 MAJOR: the Rejected list says "do not touch" for a site that row 8 rewrites.**

*Evidence:* "Rejected (do not touch) … verdict 777 <-> verify 1479", while row 8 rewrites verdict 777.

*Fix text (sub-list heading):* "Cross-boundary twins: never unify across them. Each side still applies its own side's rule, so verdict 777 takes `is_aware` (row 8), and verify 1479 and `_is_sha256_digest` stay inline."

**F-W10-9 MAJOR: several design choices are still left to Sonnet.**
- **E2 call site.** *Fix text:* "`delta = _counter_delta(old_value, new_value)`; `if delta is None: return "unknown", {}`; `if delta: deltas[...] = delta`." Add a test case in which a zero delta on every counter gives `"clear"`. `if not delta` is the plausible wrong implementation.
- **`adjudication 182`.** It has row 2's pair plus `or schema_version not in …`, and it is not listed. *Fix text:* "In scope: `not is_strict_int(schema_version) or schema_version not in _ACCEPTED_SCHEMA_VERSIONS`."
- **Membership rule.** *Fix text:* "A site is in a family iff the row's operands form a contiguous run inside one BoolOp over the same subject; other operands are kept in place."
- **`__all__`.** *Fix text:* "Add `claim_for` and `claim_carries` to `verdict.__all__`. `guards.__all__` and `records.__all__` list their public functions."
- **New dataclasses.** Dataclasses added after `be803c3a` by W6, W8 or W9 follow the same mapping. Any non-frozen or other dataclass form is BLOCKED.

**F-W10-10 MAJOR: O3 has no negative.**

*Fix text:* "Neg: at `isolation 136`, write `is_int_at_least(value, 0)` instead of `(value, 1)` → a step-1 characterization test fails."

**F-W10-11 MAJOR: the preflight is optional in the gate but required by O7, and the exclusion-line drift is invisible to the fast suite.**

*Fix text (Gate step 3):*
- "Mandatory. If the host does not permit it, write BLOCKED."
- "Before every commit, run `grep -n "pragma: no cover" src/assay/{config,liveness,mutation,mutation_witness,git}.py` and compare it with the fixture's lines."

**F-W10-12 MINOR: the obsolete known-red note (item 8).**

*Evidence:* Line 227 still carries the known red. The re-merge already happened (`1e3c8a49`).

*Fix text:* the same as F-W2-13.

**Q3, behaviour identity.** I checked every accepted cluster:
- **I1, I2 and I3** are identical in messages, exception types, bool handling, NaN, ±inf, −0.0 and short-circuit order.
  - `_SHA256_RE.fullmatch` equals the charset loop, including on a trailing `\n`.
  - `x <= k` equals `x < k+1` on ints.
  - The row 4 and row 6 algebra holds.
  - `is_percentage(nan)` refuses, as the inline check did.
- **E1–E4 and E6–E10** are identical. E3's messages are parametrised exactly, and E6 keeps the same attribute order.
- **The claims that are false:**
  - **E5** as specified: it cannot be written (F-W10-3).
  - The "None of these raises" note (F-W10-7).
  - `candidate_identity` is identical in behaviour but illegal under A-182 (F-W10-1).

**Q4:** O5 is unsound against a re-export (F-W10-5).

---

## 4. REBASE-P0-P2 — `REBASE-P0-P2.md` + `P0-measurement-hygiene.md` + `P2-loop-guards.md`

**F-R0-1 BLOCKING (P0): CD1 leaves P0's W1 and its oracles without a spec.**

*Evidence:*
- P0 W1, O1–O5b, Scope, Docs and the traceability rows are all written for `assay plan --baseline-from` in `src/assay/analysis.py`. Its refusals are `LaneConfigError` with exit 2 (P0 :118, :498), and it adds plan-payload keys.
- CD1 moves this into an `assay analyze` subcommand, but no subcommand name, argv, output shape or exit mapping exists.
  - `cmd_analyze` returns 1 on error (not 2).
  - "`assay plan` … prints how to obtain the measured estimate" contradicts O1 ("exactly one new key") if the hint goes into the JSON, and is untested if it goes to stderr.
  - A new analysis module must enter `[lanes.analysis].judge.targets` (W2 T3), but P0 forbids `assay.toml`.
- So CD1 does **not** leave P0's oracles consistent.

*Fix text (the carver must decide; recommended re-carve of P0 W1):*

> **W1.**
> - `assay analyze plan-estimate --plan <plan.json> --progress <progress.jsonl> --lane <baseline-lane>` lives in `analysis/src/assay_analysis/evidence.py`. It is no new module, so `assay.toml` stays untouched.
> - It reads `budget_per_candidate`, `jobs` and `candidate_count` from the plan JSON (`status` must be `"ok"`).
> - It applies P0's selection and measurement rules and refusals 1–8 unchanged, and adds refusal 9 for "plan status is not ok".
> - It prints `{"estimate_provenance": "measured", "estimate_source": {...}, "estimated_serial_seconds", "estimated_wall_seconds", "full_suite_central_serial_seconds", "full_suite_central_wall_seconds"}`. The `auto` budget uses `mutation.auto_budget_per_candidate_seconds`.
> - Every refusal exits **1** with `assay analyze: --progress <path>: <reason>`, via `cmd_analyze`.
> - `commit_matches_head` is dropped, because the command has no worktree input.
>
> **`assay plan`.** It adds only `estimate_provenance: "fallback"` to stdout. `--help` names `assay analyze plan-estimate`. Nothing is printed to stderr.
>
> **Oracles.** O1 is unchanged. O2–O5b are retargeted to the subcommand in `analysis/tests/test_analysis_plan_estimate.py`, with the exit codes above. The docs example goes inside an `<!-- assay-analysis-example -->` block.
>
> **Scope.** Replace `src/assay/analysis.py` with `analysis/src/assay_analysis/{evidence,cli}.py`.

**F-R0-2 MAJOR (P0): the sampler text CD4 requires.**

The W2 sampler is re-carvable. The loop classifies, then checks the timeout, then sleeps (`liveness.py:1531-1705`), so "after classification" implies "after B107's `resource_reader`". The P0 text must change as follows:
1. **Implementer:** "Sonnet for all items (operator rule)."
2. **Required flow, step 2:** "the sampler call sits immediately before `self._sleep(self._poll_interval_s)` (`liveness.py:1705`), after the hung and timeout checks. It never reads or writes `resource_trace`, `previous_resources` or `cpu_samples`. A tick that raises hung or timeout takes no sample."
3. **The `try/finally`** that writes the sidecar wraps the whole `while True:` loop, including the early `return subprocess.CompletedProcess(...)` at :1536.
4. **"The 17 existing `cpu_reader=` injections"** becomes "the existing `cpu_reader=` injections (28 at `be803c3a`, all in `tests/test_liveness_runner_monitor.py`)".
5. **O6 and O7** use `_runner(...)` (`test_liveness_runner_monitor.py:119-150`), extended with a keyword `sampler=None` that is passed to `LivenessRunner`. The O6 ordering spy records `cpu_reader`, `resource_reader`, `sampler` and `sleep` in each tick and asserts that exact order.
6. **Line numbers:**
   - stale-cleanup tuple `:1351-1359` becomes `:1394-1402`;
   - wiring `runner.py:4420` becomes `:4427`;
   - the exclusion trap "liveness 93, 94" becomes "94, 95".

**F-R0-3 MAJOR (P0 + W2 + W4): the combined refusal order in the checker is left to the implementer.**

*Evidence:* the note says "The combined order is recorded in the P0 report".

*Fix text:*

> The order is:
> 1. argparse refusals: P0 #8 and W4's `--receipt-only` and `--tester-unified-receipt` rules;
> 2. W4's receipt check;
> 3. inside `verify_report_document`: producer exit, root object, commit, lane, rigor and tree;
> 4. W2's `verify_scope`;
> 5. outcome, exit code, claims, version and provenance;
> 6. P0's `check_campaign_scope`, in the order 9 → 1–4 → 5–7.
>
> The first failure wins.

**F-R0-4 MAJOR (P2): CD2's split is not specified.**

*Fix text (replace Q-P2-1):*

> Split the file into:
> - `tests/adapters/go/test_adapters_go_scanner_progress_guards.py`: the go.py 326, 334 and 356 cases and the go_modfile 393 cases;
> - `tests/adapters/javascript/test_adapters_javascript_scanner_progress_guards.py`: 258;
> - `tests/adapters/sql/test_adapters_sql_scanner_progress_guards.py`: sql_lex 197 and 280;
> - `tests/core/test_isolation_scanner_progress_guards.py`: isolation 1406.
>
> The shared `_StepLimit`, the trace budget and the mutant builder move verbatim into `tests/scanner_progress_support.py`, imported as `from scanner_progress_support import …`. `tests/` is on `sys.path` via the root conftest.
>
> Each file asserts that its CASES name only its own component's source paths. Without that assertion, a misfiled case still passes: the plausible wrong implementation.
>
> Update P2's Touch list and gate list to these files.

**F-R0-5 MINOR (P2): `test_errors.py` stays at the root.**

*Evidence:* The note says "`test_errors.py` moves to core", but W3's `ROOT_PINNED` keeps it at the root (imported by `test_verdict_conformance.py:296`).

*Fix text:* "`test_errors.py` stays at `tests/` (W3 ROOT_PINNED)."

**F-R0-6 MINOR: R0.1 and R0.2 are already done.**

*Evidence:*
- R0.1 is done at `55611561`: the fixture is `[94, 95]`.
- R0.2 is done at `1e3c8a49`: `35adca38` is in HEAD.

*Fix text:* "DONE (`55611561`, `1e3c8a49`)." Remove "Do on landing before stage 2".

**F-R0-7 MINOR: the "unchanged `test_cli_run` 463/529/586/645" anchors are gone at HEAD.**

Both tests were deleted. Mark them "n/a at HEAD (deleted)". Work 3 and O6 were already dropped, so this is consistent.

**F-R0-8 MINOR: the obsolete known red.**

P0's and P2's own gate sections inherit the plan §4 exception. Add "No known red at HEAD", as in F-W2-13.

**Q6 answers.**
- **Anchor deltas:** correct, apart from F-R0-5 to F-R0-7.
- **P0's W2 sampler:** re-carvable, with the text in F-R0-2.
- **CD1:** it does not leave P0's oracles consistent (F-R0-1).

---

## 5. Obsolete B107 known-red note (item 8)

These briefs still rely on it:
- W2 :147;
- W10 :227;
- plan §4;
- W3 :135 and W4 :173 (outside this review's scope; fix them the same way).

`35adca38` is in HEAD (merge `1e3c8a49`), so the test does not exist.

## 6. Verdicts

| Document | Verdict | Must fix before dispatch |
|---|---|---|
| `W2-analysis-package.md` | **READY-WITH-FIXES** | F-W2-1, F-W2-2 and F-W2-3 (blocking); F-W2-4 to F-W2-8 |
| `REBASE-P8.md` (+ P8 brief + draft) | **NOT READY** | F-P8-1, F-P8-2 and F-P8-3 (blocking); F-P8-4 to F-P8-8. Fold into one self-contained W9 brief (F-P8-9) |
| `W10-dry.md` | **READY-WITH-FIXES** | F-W10-1 to F-W10-4 (blocking); F-W10-5 to F-W10-11 |
| `REBASE-P0-P2.md` | **P2/W6: READY-WITH-FIXES** (F-R0-4 and F-R0-5). **P0/W8: NOT READY** | F-R0-1 (blocking, needs a carver decision); F-R0-2 and F-R0-3 |
