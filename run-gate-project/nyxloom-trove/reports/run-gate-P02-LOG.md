# run-gate-P02 — chronological LOG

Bundle: RG-24, RG-23, RG-21, RG-25, RG-26 (five backlog entries, one file).
Worktree `/workspaces/vbpub/.worktrees/run-gate-P02-checkpoint3-bundle`,
branch `fix/run-gate-P02-checkpoint3-bundle`, based on `main` at `858766d1`.
One entry per commit. Verdicts pasted verbatim in
[`run-gate-P02-REPORT.md`](run-gate-P02-REPORT.md).

---

## 0. Orientation and baseline (no commit)

Read `AGENTS.md`, `run-gate-project/README.md`, `SPEC.md`,
`run-gate.toml` (its header explains the deliberate HOST-mode gate), and the
FULL backlog text of all five entries plus RG-1/RG-3/RG-17 for precedent.

Ran the gate on the untouched base commit **before touching any code**:

```
run-gate: rev 23 | lane selftest | env built-in 'host'
FAILED tests/test_run_gate.py::TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane
1 failed, 212 passed, 2 skipped, 2 warnings in 38.45s
run-gate: lane 'selftest' exit 1
EXIT=1
```

**The gate is RED at `main` before this package starts.** Root cause found and
confirmed independently from the primary checkout: `cmru/run-gate.toml`
`[lanes.assay]` pins `tools/assay/assay-2.2.0.pyz.sha256` while
`cmru/tools/assay/` vendors only the **2.3.0** pair (`git ls-tree` confirms
only 2.3.0 is tracked), so `validate-pointers ../cmru/cmru.toml` exits 2.
Outside this package's scope (`cmru/`), so **filed as RG-29** and NOT fixed.

Consequence recorded up front: the selftest lane's argv is
`pytest … && coverage_gate`, so a red pytest **short-circuits the
diff-coverage floor**. Every item below therefore also ran the coverage gate
explicitly with only that one pre-existing failure deselected, to obtain a
real diff-coverage verdict.

Second finding from the baseline: **new `run-gate.py` lines are only counted
as covered when exercised IN-PROCESS.** Almost all existing tests drive the
tool through `subprocess`, which coverage does not see. Every item below
therefore pairs its end-to-end subprocess tests with in-process unit or
`run_gate.main([...])` tests.

---

## 1. `bd1a3f85` — **RG-24**: exec-mode container names resolve from the judged worktree

`fix(run-gate): RG-24 -- exec-mode container names resolve from the judged worktree`

- `resolve_container_name()` takes the judged `worktree` (already threaded
  through `run_exec_lane`) and resolves `<worktree>/ciu.global.toml` →
  `<repo>/ciu.global.toml`. Declared `container_name` still wins outright.
- Resolution source gained a SCOPE label (`judged worktree:` / `repo:` +
  path) and moved into the pre-execution `container …` disclosure line, not
  only the not-running refusal.
- Missing-config refusal names BOTH candidate paths when they differ.
- Docs: SPEC `R-14a`, CONSUMERS "Python app estate with its own runner"
  (worked disclosure line + "do not pin `container_name` as a workaround"),
  CHANGES, backlog RG-24 → FIXED. rev 23 → 24.
- Tests: `TestWorktreeScopedContainerName` ×10 (5 end-to-end, 5 in-process).
- **Amended once**, before any gate claim: the first commit's tests were all
  subprocess-driven and the diff-coverage gate reported `1/9`. Five
  in-process unit tests were added and folded into the same commit.

Gate after: 222 passed + the RG-29 failure; diff coverage **9/9 (100%)**.

---

## 2. `c55f5748` — **RG-23**: env-forward breaking change declared, drift sweep widened

`fix(run-gate): RG-23 -- declare the env-forward breaking change and widen the drift sweep`

- Documented the breaking change with its migration: SPEC `R-24a`,
  CONSUMERS "BREAKING CHANGE — migrate if you use `mode = "exec"`" (pasteable,
  both halves), README env-forwarding bullet. The implicit
  `MOCK_MODE`/`RUN_LIVE_TESTS` names deliberately do NOT return.
- Answered the entry's open question **EXTEND** (not "document the
  limitation"): `scan_env_references()` replaces the line regex with an AST
  pass, adding `setdefault`/`pop`/`"X" in os.environ` and the
  helper-wrapped shape (`_env_flag_enabled("RUN_LIVE_TESTS")` whose body does
  `os.getenv(name, "")`). Bound-method `self`/`cls` offsets accounted for.
  Still advisory (exit 0). `R-24b` documents what it still cannot see.
- Unparseable file → named, with a fallback to the old regex.
- Estate audit performed: **no** vbpub project declares `mode = "exec"`,
  `forward_env`, or references either name in any `run-gate.toml`. Kept as a
  test (`TestEstateExecForwardEnvAudit`), not a note.
- Tests: `TestEnvReferenceScan` ×9 + the estate audit. rev 24 → 25.

Gate after: 232 passed + RG-29; diff coverage **68/68 (100%)**.

---

## 3. `9adf11fc` — **RG-21**: doctor names the linked-worktree host-lane git view

`feat(run-gate): RG-21 -- doctor names the linked-worktree host-lane git view`

- Directions **2 and 3** of the entry. Direction 1 (mount the common gitdir /
  hand over `GIT_DIR`) is harness-side (`shared-ramdisk-depot-manager/tools/
  gate.sh`) and deliberately not built here.
- `linked_worktree_gitdir()` returns the gitdir only when the gitfile's target
  lies OUTSIDE the tree; `None` for both benign shapes.
- `doctor` emits ONE `[WARN]` naming worktree, gitdir, the exact symptom
  (`not a git repository: …`) and three remedies. Never moves doctor's exit
  code. **Scoped to projects declaring a host lane**; with a host lane and a
  plain checkout it records `[OK]` so a reader can tell it ran.
- Docs: SPEC `R-30a`; CONSUMERS "Host lanes that delegate to a
  host-path-mounting harness (RG-21)" with the real srdm error verbatim and
  three pasteable harness fixes.
- Tests: `TestLinkedWorktreeHostLaneWarning` ×7. rev 25 → 26.

Gate after: 239 passed + RG-29; diff coverage **87/87 (100%)**.

---

## 4. `9a403da3` — **RG-25**: doctor/`--check-env` preflight assay-lane toolchain fitness

`feat(run-gate): RG-25 -- doctor/--check-env preflight assay-lane toolchain fitness`

- `build_env_probe_argv()` — the ONE in-environment probe builder, reusing
  `resolve_container_name()` (exec) and `physical_path()`/
  `dual_mount_flags()` (ephemeral). Ephemeral probes carry
  `--cgroup-parent`; no slice derivable → SKIP rather than run unconfined.
- `assay_toolchain_findings()` emits one `(status, topic, detail)` per assay
  lane; `cmd_doctor` feeds them to its existing `record()`, `cmd_check_env`
  prints them and exits **2** on a FAIL while its env-drift half stays
  advisory. Doctor's summary now counts SKIPs.
- **DEVIATION, flagged**: tools checked are `external_tools` ∪ `argv0` ∪ a
  `language` table, not `external_tools` ∪ `argv0` alone. Reasoning in the
  REPORT (§Design decisions D4).
- Tests: `TestAssayToolchainFitness` ×18, driven through a docker shim that
  actually EXECUTES the probe script. rev 26 → 27.

Gate after: 257 passed + RG-29; diff coverage **182/182 (100%)**.

---

## 5. `7b30bc49` — **RG-26**: `--base REF` → `assay run --request-base`

`feat(run-gate): RG-26 -- --base REF reaches a delegating assay lane as --request-base`

- `--base REF` on every lane invocation; `plan_comparison_base()` decides
  what the lane gets. Delegation DERIVED from `assay lanes --json` (RG-25's
  probe) — **no new `run-gate.toml` key**.
- Default ref = judged worktree's `git merge-base HEAD @{upstream}` via
  `derive_upstream_base()` (deliberately not `git_out`); no upstream → exit 2
  with the entry's exact message. No fallback to `HEAD`.
- Conjunction propagation via a `{base}` token (RG-1/`R-25`'s mechanism).
  Command lane without the token + `--base` → exit 2.
- Non-delegating assay lane + `--base` → exit 2 naming the declared
  `base_source`. Old judge + `--base` → exit 2 naming assay 3.2.0 (B044);
  old judge without `--base` → unchanged.
- **Incidental fix, RG-28 filed+FIXED here**: `run_host_lane` raised
  `KeyError('argv')` for `kind = "assay"` on `environment = "host"` — a
  config `_validate_lane` ACCEPTS, so a traceback for a legal declaration
  (`R-04` calls that a defect). Needed because RG-26's contract covers every
  assay lane.
- **RG-29 filed OPEN** (cmru-side pin, the pre-existing red gate).
- Suite helpers `lane_runs()`/`lane_execs()` added: an assay lane now issues
  a probe before the judged run, so `docker_runs(log)[0]` is no longer the
  lane. Four existing tests were asserting against the probe; one
  (`test_exec_assay_lane_judges_selected_worktree`) still PASSED against it
  because both scripts `cd` to the same tree, and now asserts
  `--verdict-json` to pin the judged exec.
- Tests: `TestComparisonBasePassthrough` ×16. rev 27 → 28.
- **Amended once**, before any gate claim: two lines
  (`assay_inventory_entry`'s docker-absent return, `run_exec_lane`'s assay
  branch) were uncovered at `241/243`; two in-process tests were added and
  folded into the same commit.

Gate after: 273 passed + RG-29; diff coverage **243/243 (100%)**.

---

## 6. Reports (this commit)

`nyxloom-trove/reports/` created for this project (it had none). LOG +
REPORT written with the per-commit gate sweep pasted verbatim: each of the
five commits was checked out detached and re-gated on a clean tree, so every
verdict in the REPORT is a real run of that exact commit, not a
reconstruction.
