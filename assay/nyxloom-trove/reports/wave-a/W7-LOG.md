# W7 LOG: shallow snapshot for both B105 lanes (B128)

Branch `wave-a-w7-shallow`, project dir `assay/`. Base `08d059ad` (W4 head `21a7b1f0` merged with landing).

## Commits

| Commit | Hash | Content |
|---|---|---|
| Step 0 | `a4a0010a` | `tests/core/test_import_contracts.py`: root layout check allows `__init__`-free `*_support.py` helpers (CD23 amended; W4+W6 integration) |
| **FULL** | `c1c0e817` | `tests/core/test_snapshot_history_shallow.py`; lanes still `snapshot_history = "full"` |
| **SHALLOW** | `a3a6d733` | `assay.toml` both B105 lanes to `"shallow"` plus rationale comment; `gate/tests/test_self_lane.py` pin to `"shallow"` |
| Docs | `157421f2` | `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, `CHANGES.md` `[Unreleased]`, `4-backlog.md` B128 status |
| LOG | (this file's commit) | LOG only, no code |

## Oracles

| Oracle | Positive result | Deliberate break | Break result |
|---|---|---|---|
| Layout check (step 0) | `test_the_tests_root_holds_only_conftest_and_fixtures` passes | plant `tests/test_x_support.py` | red; plant removed |
| Layout check (step 0) | same | plant `tests/helper.py` | red; plant removed |
| Shallow negative test | `test_shallow_snapshot_has_no_parent_of_head` passes (also asserts the source repo has `HEAD~1` and the snapshot's `HEAD` equals the judged commit) | force `HISTORY = "full"` in the test | red (`AssertionError`, `HEAD~1` resolved); reverted |
| Lane pin | `gate/tests/test_self_lane.py` 25 passed | flip `[lanes.self-qualification-preflight.isolation]` back to `"full"` | red (`test_preflight_measures_the_same_complete_source_inventory_before_r2`, identical-isolation check); reverted |
| Lane pin | same | flip both lanes back to `"full"` | red (that test plus `test_self_qualification_is_full_source_r0_through_r3`); reverted |

Focused set at FULL and at SHALLOW: `gate/tests/test_self_lane.py`, `tests/core/test_snapshot_history_shallow.py`, `tests/core/test_isolation*.py`, `tests/core/test_import_contracts.py`: 182 passed each time.

`git status --short --ignored assay` before the FULL commit: the new test showed `??`, no `!!`.

## Collect counts

- Before (base + step 0): `tests` 5437 (5438 minus the one new test).
- After (FULL, SHALLOW, docs): `tests` 5438; `gate/tests` 350.
- One full serial run `tests gate/tests` at the docs commit: 5776 passed, 12 skipped, 1 failed (as transcribed; these sum to 5789, one more than the 5788 that `pytest tests gate/tests --collect-only -q` reports at this commit, and the run's summary line was not retained, so treat them as approximate). The failure is the known environmental `tests/core/test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` (stray `/tmp/.git`, B134). Not W7's.

## For the controller's gate run

`FULL=c1c0e817 SHALLOW=a3a6d733`

Run `self-qualification-preflight` at each commit. Both runs must end with `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1`; a red or refused run is a finding, not a pass. (The only difference between the two commits is the two `snapshot_history` values, the comment and the pin.)

**The preflight cannot show pytest counts.** A green lane command returns without output tails (`src/assay/runner.py:1309-1319`), so the PASS verdict has no `result_stdout_tail` (`src/assay/verdict.py:5436-5437`), and the progress stream's `command_finished` event carries only outcome, reason code, return code and times (`src/assay/runner.py:3095-3108`). Counts are therefore not recorded from the two preflight runs.

**Skip drift is closed statically.** The only skip sites in `tests/` are `tests/core/test_isolation.py:1650` (`/proc/self/fd`), `:1654` (`RLIMIT_NOFILE` headroom), `:1704` (root) and `tests/core/test_mutation_judge_identity_properties.py:40-41` (`importorskip("hypothesis")`); `tests/conftest.py` has no skip hook and only a static `collect_ignore_glob`. None depends on repository history, and no judge test runs git against the snapshot's own repository (REVIEW-W7 check 1), so a history dependence could only show as a red preflight.

**Counts (plan §3).** Run `drift_counts.py` (Appendix below) once on the host, serially, under `nice -n 19 ionice -c3`, while no `run-gate-*` container is running:
`python drift_counts.py /workspaces/vbpub/.worktrees/wave-a-w7-shallow a3a6d7330ab2402f6f4d877b0bc82bedf50010f0 <scratch dir outside the repository>`
It materializes that one commit twice through `assay.isolation.prepare_snapshot` with the lane's own isolation policy (`full`, then `shallow`), runs `python -m pytest tests -q -rs -p no:cacheprovider` in each snapshot's `assay/` with `PATH` only, and prints both summary lines and every `SKIPPED` line. The two summary lines and the two `SKIPPED` sets must be identical; any difference is a finding.

**FULL-leg headroom.** At `c1c0e817` the full closure is 57,266 objects and 1,058,757,535 uncompressed bytes, 98.6% of the default `max_total_object_bytes` (1,073,741,824). The FULL run fits only at exactly `c1c0e817`: do not move it to a newer landing or main commit (the union of main and this branch is already 98.89%). A `SNAPSHOT_LIMIT_EXCEEDED` refusal on a moved FULL commit is an infrastructure result, not W7 drift.

No `run-gate.py` was run and no container was started.

## Drift proof (local, both legs)

The controller cancelled the two preflight runs; the fixer ran `drift_counts.py` (Appendix, verbatim) locally at FULL commit `c1c0e817c6f50a83ed639e5aaba8b96c86e5da91`, whole `tests` suite, both legs from that one commit through the lane's own isolation policy (`full`, then `shallow`), serially under `nice -n 19 ionice -c3`:

```
full: is-shallow=false rc=1 summary='1 failed, 5437 passed, 1 skipped in 200.69s (0:03:20)'
  full SKIPPED [1] tests/core/test_mutation_judge_identity_properties.py:40: could not import 'hypothesis': No module named 'hypothesis'
shallow: is-shallow=true rc=1 summary='1 failed, 5437 passed, 1 skipped in 195.38s (0:03:15)'
  shallow SKIPPED [1] tests/core/test_mutation_judge_identity_properties.py:40: could not import 'hypothesis': No module named 'hypothesis'
```

Each leg accounts for 1 failed + 5437 passed + 1 skipped; the skip is the module-level `importorskip`, so it is not one of the 5438 collected items. The two summaries are identical apart from wall time, and the `SKIPPED` sets are identical (the `hypothesis` importorskip on this host). The one failure in each leg is the known environmental B134 test (`tests/core/test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused`, stray `/tmp/.git`); the script does not print failure ids, so this was confirmed by running that file alone on the host (exactly that one test fails, 16 pass). Verdict: no drift. Scratch snapshots were removed afterwards.

## Deviations

- README was not edited: it describes only the generic shallow default and never the B105 lanes' snapshot mode.
- Step 0's test text was applied exactly as specified.
- Follow-up (REVIEW-W7 W7R-4): the step-0 root-layout regex also admits a directory named `*_support.py`; add `and (TESTS_ROOT / name).is_file() and not (TESTS_ROOT / name).is_symlink()` to its filter in a later package (after W5 and W7 have both merged, so the identical step-0 hunks do not conflict). A collectable test inside such a directory is already refused by `test_every_test_file_is_in_its_component_folder`.

## BLOCKED

None.

## Appendix: drift_counts.py

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

READY-FOR-GATE 9e1a932f
