# W1-WI5 — CMRU real-consumer qualification: harness landed, O6/O7 BLOCKED

**Status: the harness is complete, independently tested, and has been run
for real against the installed candidate wheel in `tester-unified`. It
found a genuine defect in shipped `assay` code, not in itself or in
B006(a)'s own omission mechanism. O6 and O7 do not honestly pass. This is
reported per the work item's own instruction rather than arranged around:
"if it cannot be made honest, that is a finding worth more than a green
tick."**

## 1. What was built

- `assay/gate/python/qualify_cmru_b006a.py` — the qualification harness,
  following `qualify_topos.py`'s installed-wheel/disposable-repository
  pattern. Unlike `qualify_topos.py`, it exports the **whole** repository
  tree (not a `.gitignore`-plus-`topos` subset) and never deletes the three
  unsafe Topos symlinks — B006(a)'s own property is that they stay tracked.
- `assay/tests/test_gate_qualify_cmru_b006a.py` — 59 tests, stubbing exactly
  the four genuinely environment-bound boundaries (`run_m20_preflight`,
  `run_repaired_node`, `invoke_qualification_lane`,
  `probe_snapshot_omissions`); every other function (input-drift refusal,
  full-repository export, the disposable repo's git history, every diff
  check, both pure verification functions) runs against REAL `git`,
  including one test that drives the REAL, unmodified `assay.isolation`
  module end to end.
- `assay/tools/tester-unified-gate.sh` — the new phase, inserted immediately
  after `ASSAY_GATE_PHASE=topos-qualified` and before
  `run_independent_witness`, per O7. **Landed as specified even though it
  currently reddens the gate** — see §6. Reverting it would ship WI-5
  without even attempting O7, which is a worse dishonesty than a red phase
  with a full diagnostic.

### Coverage

```
Name                                Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------------------
gate/python/qualify_cmru_b006a.py     250      0     94      0   100%
-------------------------------------------------------------------------------
TOTAL                                 250      0     94      0   100%
```

No `pragma: no cover` anywhere in either new file.

### Full suite, foreground, exit code captured directly

```
2899 passed, 11 skipped, 1 warning in 204.07s (0:03:24)
EXIT=0
```

(Baseline at `ee88ca23` was 2840 passed, 11 skipped; the delta is exactly
this work item's 59 new tests.)

## 2. Frozen inputs — re-frozen at the current revision

The carve's own §6 named `c3b00729...`, many commits stale. Re-frozen and
re-verified against `HEAD` (`ee88ca23...`) directly:

```
INPUT_REVISION=ee88ca2328d99ce81046b1ce1e4bb33667093147
CMRU_TREE=6fbb3c2c00be81dd893dc11ad0109d14bc846556
TOPOS_TREE=31b88ee2ff71566afa4aa23b83ddeff5799ec855
```

`verify_pinned_inputs` refuses any input revision, CMRU tree, Topos tree, or
release-test-file byte content other than these exact values (unit-tested:
`test_verify_pinned_inputs_refuses_a_wrong_input_revision_git_cannot_resolve`,
`test_verify_pinned_inputs_refuses_a_resolvable_oid_with_the_wrong_spelling`,
`test_verify_pinned_inputs_refuses_a_wrong_cmru_tree`,
`test_verify_pinned_inputs_refuses_a_wrong_topos_tree`,
`test_verify_pinned_inputs_refuses_a_drifted_release_test_file`).

The three omitted symlinks, re-confirmed present (not deleted) in the
disposable seed:

```
topos/tests/fixtures/inspect_files/_danger/passwd_link
topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape
topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current
```

## 3. M20 — my own receipt (the carve's open question, re-closed)

Real `tester-unified:local`, `--network=none`, `--cgroup-parent=dev-background.slice`,
the exported `cmru/` tree plus its two root dependencies
(`cmru.project.sample.toml`, `cmru.release.sh`) patched with exactly the one
insertion, run from `cmru/`:

```
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /opt/tester-venv/bin/python -m pytest tests -q
```

```
1400 passed, 2 skipped in 17.33s
M20_EXIT=0
```

This is consistent with the orchestrator's own receipt (unmodified suite:
`1 failed, 1399 passed, 2 skipped`; the one failure is exactly
`test_release_publish_rejects_response_without_upload_coordinate`, which the
one-line repair converts to a pass — 1399 + 1 = 1400). The three
`TestConsulBackend` socket errors reported in the restricted carving cockpit
do **not** reproduce in `tester-unified`; they were cockpit-only, exactly as
the orchestrator's own independent M20 run found. The repair is necessary
and sufficient; this is not re-derived, it is re-run.

Note also: on my FIRST attempt at this receipt I exported only `cmru/`
without its two repo-root dependencies and got
`FileNotFoundError: cmru.release.sh` — an independent, real-environment
re-confirmation of M8's finding that both root files are load-bearing.

## 4. The real qualification run — through the installed wheel, in tester-unified

Built via the SAME closure `tester-unified-gate.sh` uses (private exact-OID
sparse clone, offline five-wheel build, `run-venv` install), then invoked
exactly as O6 specifies:

```
PYTHONPATH= "$scratch/run-venv/bin/python" \
  "$worktree/assay/gate/python/qualify_cmru_b006a.py" \
  --source-repo "$worktree" \
  --scratch "$scratch/b006a-cmru" \
  --current-assay "$scratch/run-venv/bin/assay" \
  --current-version 1.0.1.dev300+gee88ca23
```

Wheel built and installed cleanly (`ASSAY_GATE_PHASE=wheel-installed`). The
harness ran through: input verification, full-repository seeding (3213
tracked paths, all three unsafe symlinks present with `/etc/passwd`
targets), the qualification-baseline repair (byte-exact, diff exactly one
file), the controlled head commit (diff under `cmru/src` exactly
`_b006a_probe.py`; full diff exactly the three declared files), and the
real `assay run cmru_b006a_qualification` invocation. It stopped at its own
`check_qualification_artifact` oracle:

```
QualificationError: expected the qualification lane to PASS, got FAIL/UNCOVERED_LINES
O6_EXIT=1
```

**The four verdicts, exactly as the installed wheel produced them**
(`commit: 5e007b1d427194a80a308aabc9280e158de3f52a`):

| Rigor | Outcome | Reason code |
|---|---|---|
| R0 | PASS | — |
| R1 | **FAIL** | **UNCOVERED_LINES** |
| R2 | PASS | — |
| R3 | INCONCLUSIVE | CANARY_INCONCLUSIVE |

`snapshot_policy` in the artifact is exactly:

```json
{"selection": "repository-minus-unsafe-symlinks",
 "unsafe_symlink_omissions": [
   "topos/tests/fixtures/inspect_files/_danger/passwd_link",
   "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
   "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current"]}
```

matching the declared lane exactly.

### R2 — the mutant identity and kill (real)

```json
{"candidate_count": 1, "total": 1,
 "killed": [{"path": "cmru/src/cmru/_b006a_probe.py", "lineno": 2,
             "operator": "python:compare-swap", "description": "Eq->NotEq",
             "start_byte": 50, "end_byte": 52}],
 "survived": [], "crashed": [], "budget_exceeded": [], "equivalent": []}
```

One candidate (`value == 7` → `value != 7`), killed by
`assert matches(7)`. R2 genuinely PASSED — it does not depend on the
coverage artifact at all, only on the mutated suite's own exit code, and it
proves the omission-mode snapshot really ran the real test suite against
the real mutated source.

### R3 — the control/transformed pair (real)

```json
{"control_outcome": "FAIL", "mechanism": "uncovered-line",
 "target": "src/cmru/_b006a_probe.py",
 "reason_code": "CANARY_INCONCLUSIVE",
 "description": "the lane's baseline R1 coverage measurement did not PASS -- an uncovered-line canary control has no known-good coverage baseline"}
```

Expected R3 shape per §6: control PASS, transformed FAIL,
`expected_reason_code == observed_reason_code == UNCOVERED_LINES`.
**Observed:** the canary control itself is FAIL (inherited from R1's own
FAIL, per its own stated precondition), so assay correctly refuses to run
the transformed half at all and renders `INCONCLUSIVE/CANARY_INCONCLUSIVE`
instead of fabricating a control/transformed comparison against a baseline
that never passed. This is R1's failure propagating exactly as designed —
not a second, independent defect.

### The three omitted links — proven absent in the snapshot the command actually saw

The live run never reached `probe_snapshot_omissions` (it stopped one step
earlier, at the R1 check). Two independent, real proofs instead:

1. **Direct, live, tied by commit hash.** Re-running `seed_disposable_
   repository` → `apply_qualification_baseline_repair` → `build_
   controlled_head` from the identical frozen inputs is fully
   deterministic (fixed author/committer identity+date), and produced the
   **identical** `head_oid = 5e007b1d427194a80a308aabc9280e158de3f52a` as
   the real tester-unified run above. Calling `probe_snapshot_omissions`
   directly against that exact commit (via the real `assay.isolation`
   module, real `prepare_snapshot`/`materialize`):

   ```json
   {"omitted_absent": [true, true, true],
    "cmru_root_present": true,
    "topos_ordinary_present": true,
    "status_clean": true}
   ```

2. **Indirect but load-bearing.** R0 and R2 both genuinely ran the real
   1401-test suite (R2 twice: baseline and the one mutant) against this
   same disposable snapshot. Had the omission not held, P22's own
   commit-validation would have refused the whole lane with
   `ERROR/GIT_FAILED` naming the first absolute Topos symlink, before any
   unit ran at all (§3.6 of the carve) — R0/R2 could not have PASSED.

## 5. Proof the real `cmru/` and `topos/` trees are unchanged

`git status --porcelain=v1 -- cmru topos` against the real
`/workspaces/vbpub/.worktrees/assay-B005-B006-coverage-v6` checkout, before
and after every run in this report (harness's own automatic check, plus
manually re-verified): **empty, both times, every time.** No file under
`cmru/` or `topos/` was ever written by anything in this work item.

## 6. The finding — R1 `changed_lines` coverage is broken for any nested-project lane

This is not a defect in the harness, and not a defect in B006(a)'s snapshot/
omission mechanism (R0's clean run and R2's real kill both depend on that
mechanism and both PASSED). It is a pre-existing gap in
`assay.evaluate.evaluate_coverage` / `assay.runner.evaluate_r1`, first
exposed because WI-5 is the first time a genuinely nested project
(`cmru/assay.toml`, `project_prefix = "cmru"`) has run R1 `changed_lines`
coverage through a **real subprocess** rather than a hand-built profile.

**The mechanism.** `evaluate_r1` (`runner.py`) runs the command with
`cwd=snapshot.project_root` (line 1318) — e.g. `.../cmru`. It diffs
`base..head` with `repo=baseline_snapshot.root` (the snapshot's REPO TOP),
so `added.by_file` keys are repo-top-relative:
`"cmru/src/cmru/_b006a_probe.py"`. It reads the coverage artifact from
`project_root` — but the artifact's own keys are whatever the coverage
tool wrote them as, relative to the CWD it actually ran under
(`project_root`), i.e. project-relative: `"src/cmru/_b006a_probe.py"`
(verified three independent ways below). `evaluate_coverage`'s
`_normalized_profile_files` calls
`adapter.normalize_coverage_key(raw_key)` to reconcile the two spellings —
but `PythonAdapter.normalize_coverage_key`
(`src/assay/adapters/python.py:822-826`) only **strips** a configured
`coverage_key_prefix`; it never **adds** one. Its own doc comment says the
default (`coverage_key_prefix=""`) is fine because it is
*"the common case where coverage.py's own cwd already matches the diff's
own repo top"* — true only when `project_prefix == ""`, i.e. `assay.toml`
sits at the repository root. `coverage_key_prefix` is never set from
`project_prefix` anywhere in the real CLI/runner wiring (confirmed: it is
constructed only in two unit test files,
`tests/test_adapters_python_normalize_coverage_key.py` and
`tests/test_adapters_python_union_fidelity.py`, never in `cli.py` or
`runner.py`). The lookup therefore misses for every changed file in a
nested project, and `evaluate_coverage` correctly (from its own point of
view) reports 0% — the file simply is not in `cov_by_repo_path` under the
key it looked up.

**Why every existing test missed this.** `tests/test_runner_snapshot_
selection.py`'s own `_write_fake_coverage` (WI-3's comprehensive O3 test,
which DOES use `project_prefix="cmru"`) fabricates its coverage.json
directly and its own comment says why:

> `# Repo-top-relative key, matching git diff's own spelling (A-145) --
> the same reason _recording_process_runner above uses "cmru/..." -- never
> the project-relative path the double's own "command" ran under.`

That comment states the CORRECT contract `evaluate_coverage` expects, but
the double never runs a real coverage tool to check whether a real one
actually produces that spelling from that CWD — it just writes what
`evaluate_coverage` wants directly. Every R1-with-nesting test in this
codebase does the same. WI-5 is the first to route a REAL `pytest-cov`
run, from a REAL nested project directory, through the REAL evaluator.

**Independent confirmation the coverage tool itself is fine, three ways**
(so this is unambiguously an assay-side key-matching gap, not a pytest-cov
quirk, not a fixture error, and not a P22 snapshot-fidelity problem):

1. A minimal two-file `cmru`-shaped project, `pytest --cov=src/cmru
   --cov-branch --cov-report=json` from its own `cmru/`: `_b006a_probe.py`
   reports `"executed_lines": [1, 2]`, 100%, key
   `"src/cmru/_b006a_probe.py"`.
2. The REAL, full `cmru/tests/` (1401 tests) plus the two new files, same
   command: identical 100%, identical key.
3. Inside a REAL P22 snapshot (`prepare_snapshot`/`materialize`, the exact
   mechanism the lane itself uses), running the exact same command: the
   raw `.assay/coverage.json` the command produced also carries
   `"src/cmru/_b006a_probe.py"` at 100%.

In all three, the artifact is correct and complete; only assay's own
repo-relative/project-relative reconciliation is missing.

**This blocks O6 and O7 honestly.** It is a `src/assay/` code defect
(`runner.py`'s `evaluate_r1`, `evaluate.py`'s `_normalized_profile_files`,
and/or `adapters/python.py`'s `coverage_key_prefix` wiring), squarely
outside WI-5's authorized file list
(`gate/python/qualify_cmru_b006a.py`, `tests/test_gate_qualify_cmru_
b006a.py`, `tools/tester-unified-gate.sh`, this report). Fixing it needs
its own design (at minimum: deciding whether `evaluate_r1` should diff with
`--relative=<project_prefix>` so diff keys become project-relative, or
whether the runtime should construct `PythonAdapter(coverage_key_prefix=
project_prefix)` — the latter cannot work as the field is strip-only today,
so the diff-side fix looks more promising, but neither choice belongs to
this work item), its own tests, and very likely its own product ruling. **I
did not make that change.** Making it inside WI-5 would be exactly
"arranging a pass" the work item explicitly forbids.

## 7. Consequently: O6 and O7 as landed

- **O6 does not pass.** The harness runs correctly end to end, drives the
  real installed wheel, and its own oracle correctly refuses a FAIL/
  UNCOVERED_LINES lane rather than reporting a false
  `ASSAY_B006A_CMRU_QUALIFIED=1`. No marker was printed, honestly.
- **O7, actually run** (`bash assay/tools/tester-unified-gate.sh .`, real
  command, real exit code, foreground, from the repository top, against
  this commit `cd83ae8d`) — pasted, not summarized:

  ```
  ASSAY_GATE_PHASE=wheel-installed
  25 passed, 16 deselected in 1.31s
  ASSAY_GATE_PHASE=attestation-hardened
  13 passed, 31 deselected in 14.39s
  ASSAY_GATE_PHASE=verdict-v5-accepted
  17 passed in 0.72s
  ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
  26 passed in 0.72s
  ASSAY_GATE_PHASE=verdict-v6-successors-verified
  tester-unified: FAIL/COMMAND_FAILED (exit 1)
    commit: cd83ae8de2bcbea65e02ed49ffeb789ec48aa533
    argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
  ASSAY_GATE_DIAGNOSTIC=self-hosted-lane-red; rerunning its command for visible diagnostics
  ...
  8 failed, 2895 passed in 270.24s (0:04:30)
  GATE_EXIT=1
  ```

  The gate does **not** even reach `topos-qualified` or the new
  `cmru-b006a-qualified` phase this time: it reds one step earlier, inside
  `run_self_hosted_lane`'s own `assay run tester-unified` (assay judging
  its own source tree through the just-installed wheel). All 8 failures are
  in `tests/test_python_qualification.py`
  (`test_run_scenario_reproduces_the_locked_missing_line_terminal`,
  `test_run_scenario_records_the_exclusion_capability_asymmetry`,
  `test_integrity_matrix_negatives_produce_their_frozen_terminals`,
  `test_universal_pass_mutation_is_rejected_by_the_whole_document_comparator`,
  `test_a_scenario_that_must_compare_with_topos_refuses_a_missing_witness`,
  `test_a_scenario_that_measured_nothing_is_refused`,
  `test_the_wrong_source_root_decoy_is_rejected_because_of_the_root`,
  `test_release_smoke_scenario_matches_the_current_full_pass_shape`) — the
  EXISTING test suite for `qualify_topos.py` (P25), each now getting an
  unexpected `ERROR` (exit 2) instead of its frozen expected terminal, or a
  missing verdict file. **This file is untouched by this work item**
  (`git log -1 -- tests/test_python_qualification.py` shows its last
  change was `f13e78a2`, 2026-08-11, days before B006(a) WI-1 started; my
  own new `test_gate_qualify_cmru_b006a.py` is not among the 8 failures and
  ran clean inside this same self-hosted pass). I did not diagnose this
  further — it is unrelated to CMRU, to nested `project_prefix` lanes, or
  to anything WI-5 touches, and chasing an unrelated pre-existing P25
  regression is outside this work item's scope. It is reported here only
  because it is real, current O7 evidence and A-232 forbids summarizing
  around it: **as of this commit, `bash assay/tools/tester-unified-gate.sh
  .` exits 1 before reaching my new phase at all**, for a reason this work
  item did not create and cannot honestly claim credit or blame for. My own
  targeted, narrower construction in §4 (wheel build through
  `wheel-installed`, then straight to `qualify_cmru_b006a.py`, skipping the
  self-hosted lane and the locked-suite phases) is what isolates and proves
  the CMRU-specific R1 finding in §6 without that unrelated regression in
  the way.
- The gate-script wiring for the new phase is landed anyway, exactly where
  §6/O7 specify (immediately after `topos-qualified`, before
  `run_independent_witness`), because shipping WI-5 without even attempting
  O7 would under-deliver the specified file list and hide, rather than
  surface, both findings above. Once the self-hosted-lane regression is
  independently resolved, the next `bash assay/tools/tester-unified-gate.sh
  .` run will reach `cmru-b006a-qualified` and fail there instead, for the
  §6 reason, until that is also resolved.

## 8. A second, smaller gap found while implementing — not in WI-5's scope either

§6 WI-5's own closing paragraph instructs: *"Also add an in-repo
integration fixture named for dstdns's exact path
`infra-global/reverse-proxy/etc-nginx/modules` ..."*, and O5 names its test
target as `tests/test_runner_snapshot_selection.py::
test_dstdns_nginx_link_is_an_exact_omittable_leaf` — a file WI-3 owns, not
WI-5. Neither the fixture nor that test exists anywhere in the tree today
(`rg -n "infra-global|etc-nginx|nginx"` across `tests/` and `src/` returns
nothing). WI-5's own authorized file list does not include `test_runner_
snapshot_selection.py`, so this was left alone rather than silently
expanded into; recorded here since O5 is presently unimplemented and would
also fail if run.

## 9. What was NOT touched

`cmru/`, `topos/`, `assay/nyxloom-trove/carve-assets/**`,
`assay/tests/fixtures/coverage/**` — all confirmed unchanged by `git
status`/`git diff` throughout this work. No `pragma: no cover` was
introduced anywhere.
