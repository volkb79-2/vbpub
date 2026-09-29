# REVIEW-W7: shallow snapshot for both B105 lanes (B128)

**Reviewer:** fresh adversarial session (Claude Opus 5.5), 2026-09-29.
**Scope:** `git diff 08d059ad..wave-a-w7-shallow` (`a4a0010a`, `c1c0e817`, `a3a6d733`, `157421f2`, `bdd9cde7`), worktree `/workspaces/vbpub/.worktrees/wave-a-w7-shallow`, project `assay/`.
**Read-only.** No repository edits and no commits. No gate and no container were run. Scratch work is in `scratchpad/w7review/`. Everything ran under `nice -n 19 ionice -c3`.

**What I ran:**
- focused pytest: `tests/core/test_snapshot_history_shallow.py`, `tests/core/test_import_contracts.py`, `tests/core/test_config_snapshot_selection.py`, `tests/core/test_docs_examples_and_vocabulary.py`, `tests/core/test_cli_analyze_seam.py`, `gate/tests/test_self_lane.py`. All green: 16 + 87 + 48 passed.
- collect-only: `tests` 5438, `gate/tests` 350, both together 5788.
- a scratch probe of the isolation API in shallow, full and shallow-with-base modes (`w7review/probe.py`).
- a scratch copy of the new test with `HISTORY = "full"`. It is red.
- `git rev-list`/`cat-file` closure measurements.
- the drift-count script in Appendix A, run on a 91-test subset of the real commit `a3a6d733` in both modes. It works: `full: is-shallow=false … 91 passed` and `shallow: is-shallow=true … 91 passed`.

---

## Check results

### 1. Hidden history readers: none found

**What I scanned.**
- Every file under `tests/`, which is what the lane argv `python -m pytest tests …` collects (`assay.toml:88-92`, `:190-194`).
- The patterns `HEAD~`, `HEAD^`, `rev-list`, `log`, `merge-base`, `describe`, tags/`refs/tags`, `--is-shallow`, `blame`, `cat-file`, `for-each-ref`, `show-ref`, reflog, `show`, `clone`, `worktree add`, `fetch`, `archive`, `ls-files`, `.git` reads and `rev-parse`. There are 189 raw hits in 29 files.
- Every `PROJECT_ROOT`/`REPO_ROOT`/`TESTS_ROOT`/`__file__`/`Path.cwd()`/`chdir` use.
- Every CLI invocation without `--file`.

**Result.**
- Every git history read in `tests/` runs against a repository the test builds under `tmp_path`: the `git_repo` fixture (`tests/conftest.py:438-479`), `_root_repo`, `_seed` or `two_repos`. None reads the project's own repository.
- `tests/conftest.py:567` `cut_snapshot_history` reads only the snapshots of those self-built repositories.
- `PROJECT_ROOT` is used only to read tracked files: fixtures, carve-assets, docs, schemas and `b105-coverage-exclusions.json`. It is never used as a git repository.
- The only test that touches the enclosing project at all is `tests/core/test_cli_analyze_seam.py:43-47`. It runs `chdir(PROJECT_ROOT)` and then `main(["lanes"])`, which is a lane-file read with no git call (`src/assay/cli.py:481-486`; `config.py` has no git call).
- No judge test builds a wheel or sdist, so setuptools-scm tag or `describe` reads cannot occur. `assay.__version__` comes from installed metadata.
- The retired readers are gone: CD5's release-tag tests (no `c56a13ea`, `every_release_since` or `embargo` remain) and A-475's Topos/`test_python_qualification.py`.

**`src/assay` paths the lanes exercise.**
- Only `git.py` and `runner.py` spawn git.
- The history readers are `resolve_base` and `base_resolution_mode` (`rev-list --parents`, `merge-base`, `git.py:792-825`), `is_ancestor`, `verify_exact_commit`, `tree_entry_kind` and `path_is_current` (`git.py:993-1083`). Tests call them on self-built repositories only.
- In the B105 lanes' own runtime, the outer `assay` calls base resolution and attestation only against the consumer repository, before any snapshot exists (`runner.py:5324-5333`, `cli.py:1363`). Both are also inactive here: the B105 lanes declare no `judge.base`, and the gate script passes no `--request-base` (`resolve_base_declaration` returns `None`, `runner.py:3504`).
- Inside a snapshot the lanes run only `status`/`dirty_paths`, `rev-parse HEAD` and `read-tree`/`commit-tree`/`write-tree` plumbing. `whole_target` skips the base diff (`runner.py:1845-1855`, `:1948`).

This static answer predicts that the drift proof shows no change.

### 2. Skips that would hide breakage: none are history-dependent

There are exactly four skip mechanisms in `tests/`:
- `tests/core/test_isolation.py:1650`: no `/proc/self/fd`;
- `tests/core/test_isolation.py:1654`: no `RLIMIT_NOFILE` headroom;
- `tests/core/test_isolation.py:1704`: running as root;
- `tests/core/test_mutation_judge_identity_properties.py:40-41`: `importorskip("hypothesis")`.

`tests/conftest.py` has no skip hook, `pytest_collection_modifyitems` or `pytest_ignore_collect`, and its only `collect_ignore_glob` (`:291`) is static. None of these depends on history, so no test can newly skip under shallow.

A caveat that matters for the drift proof: a green preflight does not report skip counts at all (W7R-1).

### 3. The negative test (`tests/core/test_snapshot_history_shallow.py`): sound

- **Same API as the lanes.** It goes through `prepare_snapshot(spec)` and `prepared.materialize()`, with the same `IsolationConfig.snapshot_history` field the TOML loader fills. This is the path the runner uses (`runner.py:5336-5345`); the policy is passed by identity (`runner._snapshot_policy_for_lane`, `:2596-2615`). It is not a hand-rolled `clone --depth 1`.
- **Selection mode.** It uses `snapshot_selection="repository"` where the lanes use `"repository-minus-unsafe-symlinks"`. That is irrelevant: `_shallow_boundaries` and `no_walk` read only `snapshot_history` (`isolation.py:1169-1177`, `:1992-2002`).
- **Red if full.** I reproduced this on a scratch copy with `HISTORY = "full"`: `AssertionError: a shallow snapshot must not resolve HEAD~1, got 'e9b2d59…'`. The constant feeds the product's own branch at `isolation.py:1992`, so the break exercises the product path, not only a test constant.
- **Not hollow.**
  - The source-side `rev-parse --verify HEAD~1` asserts that the parent exists.
  - The snapshot's `HEAD` must equal the judged commit. This also rules out a false pass from git walking up to a foreign repository, such as the stray `/tmp/.git` in B134.
  - Probe: `rev-parse HEAD~1` inside a shallow snapshot exits 128 with "unknown revision"; in full mode it resolves.
- **No clock, no globals and no monkeypatch.**
- **Redundancy (not a finding).** `test_isolation.py:538` and `:567` already pin the exact `.git/shallow` contents, `rev-list --count` of 1 versus 2, and the absence of the shallow file in full mode. The new test is spec-mandated and cheap (0.3 s).
- **Coverage gap, docs only.** The test covers `materialize` only. In an R2 mutant or R3 canary child (`materialize_replacement`), `HEAD~1` resolves to the judged commit and `HEAD~2` is absent; my probe confirmed this in both modes. W7R-2 corrects the docs that overstate this.

### 3a. Step-0 layout check (`tests/core/test_import_contracts.py:413-420`): correct for files

`re.fullmatch(r"(?!test_)[a-z0-9_]+_support\.py", name)`:

| Name | Result |
|---|---|
| `Test_x_support.py` | refused |
| `test_x_support.py` | refused |
| `test__support.py` | refused |
| `__init__.py` | refused |
| `helper.py` | refused |
| `x-support.py` | refused |
| `x_support.PY` | refused |
| `_support.py` | refused |
| `x_support.py\n` | refused |
| `__support.py` | accepted (harmless) |
| `x_test_support.py` | accepted (harmless) |
| `conftest_support.py` | accepted (harmless) |

The regex never matches pytest's default `test_*.py`/`*_test.py` collection patterns, and pyproject sets no `python_files`.

**Directory bypass.** A directory named `evil_support.py/` passes this check, and pytest 9.1.1 does recurse into it. I verified this in scratch. However, any `test_*.py` inside it is caught by the sibling check `test_every_test_file_is_in_its_component_folder` (`:242`, `misplaced()`). So no collectable test can escape; only a non-test directory could. See W7R-4, a follow-up only.

**Blob identity.** Step 0 is byte-identical to W5's `2faa6426` (blob `d13fe017` on both). W8 already contains `a4a0010a`, so the merges are clean.

### 4. Pins and config: complete

- **Both lanes match.** Their `[…isolation]` tables are textually identical (`assay.toml:101-108`, `:207-214`). `gate/tests/test_self_lane.py:136` pins `"shallow"`, and `:359` requires `preflight.isolation == qualification.isolation`. `snapshot_history` takes part in `IsolationConfig` equality; only `snapshot_history_declared` is `compare=False`.
- **No functional `"full"` pin remains** in `tests/`, `gate/tests/`, `tools/b105_report_check.py`, `tools/*.sh`, `run-gate.toml` or `nyxloom-trove/nyxloom.toml`. Only historical briefs mention it (`W1-retire.md`, `W4-test-split.md`, `W4-REPORT.md`), and they are records. `test_b105_isolation_proof_boundaries.py:33` defaults to `history="full"` in a faked unit helper, which is not a lane pin.
- **Nothing downstream records `snapshot_history`:**
  - the verdict's `snapshot_policy` holds `selection`, `unsafe_symlink_omissions` and `link_paths` only (`verdict.py:4140-4185`; there is no "history" in `verdict.schema.json`);
  - `assay lanes --json` emits `snapshot_selection` and `link_paths` only (`cli.py:2060-2063`);
  - the mutation judge digest folds the tree digest, argv, env, cwd/prefix, `link_paths` and tool version, but not history (`mutation.py:1060-1140`);
  - the S1 receipt is `{schema_version, lane, commit, tree}`;
  - the B105 checker reads none of it.

  So no old report, receipt, reuse source or resume state can break. `assay.toml` is inside the judged tree, so any toggle changes the tree digest anyway.

### 5. Docs

- **README.** The implementer's claim holds. README mentions `snapshot_history` only generically (`README.md:129-134`, `:505-508`, `:878-880`), and its B105 paragraph (`:984-1010`) never states a history mode. No README edit is needed.
- **CHANGES.** The entry is accurate and correctly says assay-internal.
- **DESIGN-GUIDE, CONSUMERS and the `assay.toml` comment** are accurate apart from the precision issues in W7R-2.

### 6. Combined-axis attack: none lands

I tried each of the following:

- **(a) `--base`/`--request-base` with a merge-base inside the snapshot.**
  - The B105 lanes declare no `judge.base`, and the gate passes no `--request-base`. `resolved_base` is therefore `None`.
  - For any other lane, A-451 carries the pre-resolved base into the seed. B101 P1 removed in-snapshot `merge-base` (`runner.py:3540-3565`), and `cut_snapshot_history` tests prove it.
  - Probe C: with a base in a shallow snapshot, the base object exists (`cat-file` succeeds) while `merge-base` exits 1, which is exactly why nothing calls it there.
- **(b) `dirty_ignore`/`--allow-dirty`.** Neither is used by B105. `.git/shallow` lives inside `.git`, so `status` and `dirty_paths` never report it.
- **(c) R3 canary.** The transformed half is a child commit of the seed. `whole_target` R1 reads no diff (`canary.py:407-437`, `runner.py:1845`).
- **(d) B106 `mutation_witness` and `--reuse-from`.** Neither `reuse.py` nor `mutation_witness.py` spawns git; reuse is keyed on verdict JSON.
- **(e) `--resume` state across the FULL to SHALLOW boundary.** The judge digest folds `tree_sha256`, which changes with `assay.toml`, so no stale replay can occur.
- **(f) Child snapshots and `HEAD~1`.** R2 and R3 child snapshots resolve `HEAD~1` (the judged commit) while the baseline does not. A future history-reading test would therefore pass in mutants but fail at the baseline, which fails loudly at R0, not silently.

The only product need for base objects is carried by A-451, and the B105 lanes have no base.

---

## Findings

### W7R-1 (MAJOR): the drift proof's count comparison cannot be observed from the preflight run as the log instructs

**Where:** `assay/nyxloom-trove/reports/wave-a/W7-LOG.md:35-39` ("For the controller's gate run"), which applies plan §3's drift proof.

**Evidence:**
- **No output tails on green.** A green lane command returns a `CommandResult` without tails: `src/assay/runner.py:1309-1319`, "including the omitted output tails". The verdict writes `result_stdout_tail` only when it is not `None` (`src/assay/verdict.py:5436-5437`).
- **No counts in progress.** The `command_finished` event carries only `outcome`, `reason_code`, `returncode`, `started` and `ended` (`src/assay/runner.py:3095-3108`). Per-test `test` events exist only in the R2 baseline (`mutation.py:2521`), which is the full lane, and running that is forbidden.
- **No counts anywhere else.** The coverage JSON carries no test counts, and the gate script prints no pytest output.

So both `self-qualification-preflight` runs yield PASS/PASS and no collected, passed or skipped numbers. The log's "compare collected, passed and skipped counts" can only be recorded hollow. That is the exact "newly skipped" blind spot plan §3 exists to close.

**Fix:** in `assay/nyxloom-trove/reports/wave-a/W7-LOG.md`, replace the whole paragraph that begins "Run `self-qualification-preflight` at each commit and compare collected, passed and skipped counts." (line 39) with exactly this:

```
Run `self-qualification-preflight` at each commit. Both runs must end with `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1`; a red or refused run is a finding, not a pass. (The only difference between the two commits is the two `snapshot_history` values, the comment and the pin.)

**The preflight cannot show pytest counts.** A green lane command returns without output tails (`src/assay/runner.py:1309-1319`), so the PASS verdict has no `result_stdout_tail` (`src/assay/verdict.py:5436-5437`), and the progress stream's `command_finished` event carries only outcome, reason code, return code and times (`src/assay/runner.py:3095-3108`). Counts are therefore not recorded from the two preflight runs.

**Skip drift is closed statically.** The only skip sites in `tests/` are `tests/core/test_isolation.py:1650` (`/proc/self/fd`), `:1654` (`RLIMIT_NOFILE` headroom), `:1704` (root) and `tests/core/test_mutation_judge_identity_properties.py:40-41` (`importorskip("hypothesis")`); `tests/conftest.py` has no skip hook and only a static `collect_ignore_glob`. None depends on repository history, and no judge test runs git against the snapshot's own repository (REVIEW-W7 check 1), so a history dependence could only show as a red preflight.

**Counts (plan §3).** Run `drift_counts.py` (Appendix below) once on the host, serially, under `nice -n 19 ionice -c3`, while no `run-gate-*` container is running:
`python drift_counts.py /workspaces/vbpub/.worktrees/wave-a-w7-shallow a3a6d7330ab2402f6f4d877b0bc82bedf50010f0 <scratch dir outside the repository>`
It materializes that one commit twice through `assay.isolation.prepare_snapshot` with the lane's own isolation policy (`full`, then `shallow`), runs `python -m pytest tests -q -rs -p no:cacheprovider` in each snapshot's `assay/` with `PATH` only, and prints both summary lines and every `SKIPPED` line. The two summary lines and the two `SKIPPED` sets must be identical; any difference is a finding.

**FULL-leg headroom.** At `c1c0e817` the full closure is 57,266 objects and 1,058,757,535 uncompressed bytes, 98.6% of the default `max_total_object_bytes` (1,073,741,824). The FULL run fits only at exactly `c1c0e817`: do not move it to a newer landing or main commit (the union of main and this branch is already 98.89%). A `SNAPSHOT_LIMIT_EXCEEDED` refusal on a moved FULL commit is an infrastructure result, not W7 drift.
```

Then append the following at the end of `W7-LOG.md`: a heading `## Appendix: drift_counts.py`, followed by the script from Appendix A of this review, verbatim, inside a python code fence.

### W7R-2 (MINOR): three docs overstate what the shallow seed holds for the B105 lanes

**Where:**
- `assay/assay.toml:78-83`;
- `assay/docs/DESIGN-GUIDE.md:2004-2009`;
- `assay/docs/CONSUMERS.md:80-83`.

**Evidence:**
- **"And resolved base" does not apply.** The B105 lanes declare no `judge.base`, and the gate passes no `--request-base`. `resolve_base_declaration` returns `None` (`runner.py:3504`), and `_shallow_boundaries` then holds the judged commit only (`isolation.py:1169-1177`).
- **"`HEAD~1` is absent there" is false for child snapshots.** It holds only for the baseline materialization. In an R2 mutant or R3 canary child (`materialize_replacement`), `HEAD~1` is the judged commit; my probe confirmed `child HEAD~1 == judged commit` in shallow mode.
- **"Identical apart from their report path and env passthrough" is untrue.** The lanes also differ in `rigor`, `budget` and the full lane's mutation and canary tables. W7 rewrapped this pre-existing sentence.

**Fix:** three exact replacements.

1. In `assay/assay.toml`, replace these lines:
```
# (A-475), so both lanes use the A-451 default snapshot_history = "shallow" (B128,
# W7): the snapshot carries only the judged commit (and resolved base), and
# tests/core/test_snapshot_history_shallow.py proves HEAD~1 is absent there. The
# two B105 lanes below are identical apart from their report path and env
# passthrough.
```
with:
```
# (A-475), so both lanes use the A-451 default snapshot_history = "shallow" (B128,
# W7): the lanes declare no judge.base, so the seed carries only the judged
# commit, and tests/core/test_snapshot_history_shallow.py proves a shallow
# baseline snapshot cannot resolve HEAD~1 (an R2 mutant or R3 canary child's
# HEAD~1 is the judged commit itself). The two B105 lanes below share their argv
# apart from the report path, their isolation and their judge targets; the full
# lane adds R2/R3 with mutation and canary, and the preflight adds the B105
# coverage-archive env passthrough.
```

2. In `assay/docs/DESIGN-GUIDE.md`, replace:
```
default (`snapshot_history = "shallow"`, B128): the seed carries the judged
commit and resolved base only, and `tests/core/test_snapshot_history_shallow.py`
proves `HEAD~1` is absent in a shallow snapshot. Snapshot refs and tags are
intentionally not copied.
```
with:
```
default (`snapshot_history = "shallow"`, B128): the lanes declare no
`judge.base`, so the seed carries only the judged commit, and
`tests/core/test_snapshot_history_shallow.py` proves a shallow baseline
snapshot cannot resolve `HEAD~1` (an R2 mutant or R3 canary child's `HEAD~1`
is the judged commit itself). Snapshot refs and tags are intentionally not
copied.
```

3. In `assay/docs/CONSUMERS.md`, replace:
```
(`snapshot_history = "shallow"`, B128; the seed holds the judged commit and
resolved base only), and snapshot refs/tags are not copied.
```
with:
```
(`snapshot_history = "shallow"`, B128; the lanes declare no `judge.base`, so
the seed holds only the judged commit), and snapshot refs/tags are not copied.
```

These edits touch no line that W8 changes: W8 differs from W7's head only at `assay.toml:312` and not in these docs, so W8 still merges cleanly afterwards.

### W7R-3 (MINOR): the W7-LOG full-run figures do not add up to the collected count

**Where:** `assay/nyxloom-trove/reports/wave-a/W7-LOG.md:33`.

**Evidence:** 5776 passed + 12 skipped + 1 failed = 5789. `pytest tests gate/tests --collect-only -q` reports 5788 at this commit (and 5438 + 350 = 5788 separately). The run's summary line was not retained, so the figures cannot be re-checked. This matters because W7R-1 makes these figures the only count evidence in the log.

**Fix:** in `W7-LOG.md` line 33, replace
`5776 passed, 12 skipped, 1 failed.`
with
`5776 passed, 12 skipped, 1 failed (as transcribed; these sum to 5789, one more than the 5788 that `pytest tests gate/tests --collect-only -q` reports at this commit, and the run's summary line was not retained, so treat them as approximate).`

### W7R-4 (MINOR, follow-up only): the root-layout check accepts a directory named `*_support.py`

**Where:** `assay/tests/core/test_import_contracts.py:419`.

**Evidence:**
- A directory `tests/evil_support.py/` passes the regex, and pytest 9.1.1 collects tests inside it (verified in scratch).
- A `test_*.py` inside it is still refused by `test_every_test_file_is_in_its_component_folder` (`:242`, `misplaced()`), so nothing collectable escapes. Only a non-test directory would slip past the "root holds only…" rule.
- Editing the line in W7 alone would conflict with W5's byte-identical step 0 (`2faa6426`, blob `d13fe017`).

**Fix:** do not edit the test in W7. Append this bullet to the "## Deviations" list in `W7-LOG.md`:
`- Follow-up (REVIEW-W7 W7R-4): the step-0 root-layout regex also admits a directory named `*_support.py`; add `and (TESTS_ROOT / name).is_file() and not (TESTS_ROOT / name).is_symlink()` to its filter in a later package (after W5 and W7 have both merged, so the identical step-0 hunks do not conflict). A collectable test inside such a directory is already refused by `test_every_test_file_is_in_its_component_folder`.`

---

## Predicted drift-proof outcome

**Tests expected to change status between FULL (`c1c0e817`) and SHALLOW (`a3a6d733`): none.** Reasons:

- **Same tests at both commits.** Both collect the identical 5438 `tests/` items. The commits differ only in `assay.toml` (two values and a comment) and in `gate/tests/test_self_lane.py`, which the lanes do not collect. No judge test reads `assay.toml` except `test_cli_analyze_seam`'s `lanes` call, which does not look at `snapshot_history` or at comments.
- **No history reads.** No judge test runs git against the snapshot's own repository (check 1). Every history read targets a `tmp_path` repository the test builds, which is identical in both modes.
- **No history-dependent skips.** The four skip sites (check 2) depend on `/proc`, `RLIMIT_NOFILE`, root and hypothesis only, so they are identical in both legs.
- **The product path does not depend on history for these lanes.** There is no base; the in-snapshot calls are only `status`, `rev-parse HEAD` and plumbing (check 1); and the probe shows both modes materialize the same tree and `HEAD`.
- **Environment.** `test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` (B134) depends on the host's `/tmp/.git`, not on history. It has the same status in both legs: pass in the gate container, fail on this host if Appendix A is run here.

**What each leg will show:**
- **Registered preflight:** both legs PASS with `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1` and no counts (W7R-1).
- **FULL leg:** fits the seed limits with about 15 MB of headroom, but only at `c1c0e817` (W7R-1).
- **Appendix A:** identical summary lines and identical `SKIPPED` sets.

A difference in any of these refutes this review's static answer and is a finding.

---

## Controller notes (not findings)

- **W7 carries W4.** The branch is W4 (`21a7b1f0`, not yet on `assay-b110-landing`) merged cleanly with landing (`08d059ad`; its tree equals `git merge-tree 21a7b1f0 75ceb9e9`), plus W7.
  - Landing plus W4 alone is red on `test_the_tests_root_holds_only_conftest_and_fixtures`, because W6's `tests/scanner_progress_support.py` meets W4's old assertion. It stays red until `a4a0010a` or W5's identical `2faa6426` lands.
  - Merge W4 and then W7 back to back, and gate after `a4a0010a` is on landing.
  - W8 already contains W7's head (`bdd9cde7`) and touches only `assay.toml:312` beyond it.
- **W7 is also a capacity fix, not only a speed fix.** A `"full"` B105 lane was already at 98% of the default 1 GiB object ceiling on landing (57,048 objects, 1,052,699,632 bytes), and main's own full closure is at 94%. At the shallow commit the seed is 5,611 objects and about 70 MiB. This is worth one line in B128's closure note. These were the only `snapshot_history = "full"` lanes in vbpub; main has none.
- **One residual, outside W7.** The verdict does not record `snapshot_history` (A-451 deferred this to v12/B079), so a verdict cannot show which mode produced it.

---

## Verdict

**MERGE-WITH-FIXES.**
- **Code.** The lane change, the pin and the negative test are correct and complete.
- **Static result.** The analysis finds no hidden history reader and no history-dependent skip.
- **Findings.** 0 BLOCKER, 1 MAJOR (W7R-1: the drift proof as written cannot produce the counts plan §3 requires), 3 MINOR (W7R-2 doc precision, W7R-3 log arithmetic, W7R-4 follow-up note).
- **Conditions.** Apply W7R-1 through W7R-4 (all are docs or log edits). Merge only after the controller's two preflight runs are green and Appendix A shows identical summaries.

---

## Appendix A: `drift_counts.py`

This was validated on a 91-test subset at `a3a6d733` in both modes: `full: is-shallow=false rc=0 '91 passed'` and `shallow: is-shallow=true rc=0 '91 passed'`.

```python
"""W7 drift proof, count half: run the B105 judge suite in a FULL and a SHALLOW
P22 snapshot of one commit, through assay's own isolation API and the lane's
own isolation policy, and print pytest's summary line for each.

Usage (from anywhere, host python with pytest installed):
  nice -n 19 ionice -c3 python drift_counts.py <vbpub-worktree-root> <commit> <scratch-dir> [pytest-args...]
Default pytest args: tests -q -rs -p no:cacheprovider
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

worktree = Path(sys.argv[1]).resolve()
commit = sys.argv[2]
scratch_parent = Path(sys.argv[3]).resolve()
pytest_args = sys.argv[4:] or ["tests", "-q", "-rs", "-p", "no:cacheprovider"]

sys.path.insert(0, str(worktree / "assay" / "src"))
from assay.config import load_lane_file  # noqa: E402
from assay.isolation import SnapshotSpec, prepare_snapshot  # noqa: E402

lane_file = load_lane_file(worktree / "assay" / "assay.toml")
policy = lane_file.lane("self-qualification-preflight").isolation
assert policy is not None
for history in ("full", "shallow"):
    scratch = Path(tempfile.mkdtemp(prefix=f"w7-{history}-", dir=scratch_parent))
    spec = SnapshotSpec(
        repo_top=worktree,
        commit=commit,
        project_prefix=PurePosixPath("assay"),
        scratch_root=scratch,
        snapshot_policy=dataclasses.replace(policy, snapshot_history=history),
        limits=lane_file.snapshot_limits,
    )
    with prepare_snapshot(spec, timeout=3600) as prepared:
        with prepared.materialize(timeout=3600) as snapshot:
            shallow = subprocess.run(
                ["git", "-C", str(snapshot.root), "rev-parse", "--is-shallow-repository"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", *pytest_args],
                cwd=snapshot.project_root,
                env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True,
            )
            tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "<no output>"
            skips = [line for line in proc.stdout.splitlines() if line.startswith("SKIPPED")]
            print(f"{history}: is-shallow={shallow} rc={proc.returncode} summary={tail!r}")
            for line in skips:
                print(f"  {history} {line}")
```
