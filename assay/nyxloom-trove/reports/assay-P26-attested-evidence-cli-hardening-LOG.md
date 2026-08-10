# P26 — attested evidence CLI hardening — implementation LOG

Implementer: fresh Sonnet xhigh, dispatched per
`.worktrees/_control/assay-P20-P32/carver/P26.md`'s `READY_FROM_SOL` section.
Branch: `feat/assay-P26-attested-evidence-cli-hardening`, based on Sol freeze
`d610dbb430391b485de1dd352a03de26b3cddf88` (parent
`233926cedd26a6e34512806e267b7141377913b2`).

## Pre-implementation verification

- Read `AGENTS.md`, `nyxloom/reference/AUTHORING.md`/`STANDARD.md`/`DOCTRINE.md`,
  the `READY_FROM_SOL` packet, the handoff, the JIT-CARVE report, the locked
  P26 packet (`README.md`, `interface-contract.json`, `skeleton.patch`,
  `git_boundary_skeleton.py`, `probe_current_failures.py`, `probe-results.json`,
  the four `expected/*-v4-template.json`, `test_acceptance.py`), and the
  current implementation of `attestation.py`, `safeio.py`, `config.py`,
  `git.py`, `runner.py`, `cli.py`, `measurability.py`, `canary.py`,
  `mutation.py`, plus the four named existing attestation test files.
- Eleven locked content hashes verified exact match against
  `fixture-manifest.json`; manifest self-hash verified
  `4cb702ddad368becd8aca55c0d5ef6ac2c55a086bb88751ff7a450d3b05352f8`.
- Premise probe (`probe_current_failures.py`) run against the frozen input:
  exact PASS record reproduced —
  `{"current_directory_descendant_change_false_pass":true,"current_git_boundary_leaves_descendant_after_pid_only_kill":true,"current_missing_parent_maps_unreadable_not_missing":true,"current_parent_key_escape_false_pass":true,"literal_directory_diff_reports_changed":true,"literal_directory_ls_tree_is_exact_tree":true,"literal_hostile_name_diff_reports_changed":true,"literal_hostile_name_ls_tree_is_exact_blob":true,"status":"PASS"}`.
- Locked acceptance baseline, run from `assay/`:
  `PYTHONPATH=src python -m pytest nyxloom-trove/carve-assets/P26/test_acceptance.py -q -p no:randomly --tb=no`
  → exactly **9 passed, 32 failed** — the required intentional baseline.

## Implementation

Touched exactly:
`src/assay/attestation.py`, `src/assay/safeio.py`, `src/assay/config.py`,
`src/assay/git.py`, `src/assay/measurability.py`, `src/assay/canary.py`,
`src/assay/mutation.py`, `src/assay/runner.py`, `src/assay/cli.py`,
`tools/tester-unified-gate.sh`, and ten `tests/*.py` files (four rewritten
attestation test files plus signature-compatibility fixes in three more).
Nothing outside the handoff's `scope.touch` was touched; `verdict.py`,
`schemas/`, `isolation.py`, `adapters/`, `gate/`, the handoff, the JIT report,
and the locked `carve-assets/P26/` packet are all byte-unchanged (verified by
`git diff --stat` against those paths returning empty, and the hash
re-verification below).

### 1. `safeio.py` — descriptor-safe bounded input
Added `read_bounded_input(project_root, relative_path, *, limit) -> bytes | None`.
Reuses the existing `_lexical_components`/`_open_root`/`_safe_bounded_read`
machinery; adds `_open_parent_chain_or_missing`, which is `_open_parent_chain`
with one difference: `FileNotFoundError` on any intermediate directory
component returns `None` instead of raising — the declared producer supplied
no directory, which is the same absence as a missing final file (A-210). Every
other failure (symlink, non-directory, permission, race) still raises
`ERROR/UNREADABLE_ARTIFACT` via the existing `_refuse` path, unchanged.

### 2. `git.py` — deadline/process-group boundary + four attestation helpers
- Added `Remaining = Callable[[], float]` (exported) and `remaining` keyword
  args to `_run_bounded`, `_resolve_repo`, `_run_bytes` (refactored to call a
  new `_run_raw` that returns the raw `(returncode, stdout, stderr)` without
  interpreting exit code), `run`, `repo_top`, `head_rev`, `_resolve_revision`,
  `resolve_base`, `dirty_paths`.
- `_run_bounded` now: samples `remaining()` before `Popen` and before every
  selector/wait; starts the child with `start_new_session=True`; on deadline
  expiry, stdout/stderr overflow, or any other abnormal exit, SIGKILLs the
  whole process group via `_kill_owned_group` (which unconditionally attempts
  `killpg` regardless of `proc.poll()` state — a forked descendant can hold
  pipes open after the direct child exits); re-raises the exact exception
  object `remaining()` raised; never starts another child after expiry.
- Added `verify_exact_commit`, `is_ancestor`, `tree_entry_kind`,
  `path_is_current` — each with a **required** (non-optional) `remaining`
  keyword, built on `_run_raw` so they inherit the same
  `--literal-pathspecs`/git-dir/work-tree/replacement-env boundary every other
  call in this module uses. `verify_exact_commit` additionally checks the
  resolved OID is byte-identical to the declared one (catches an annotated
  tag peeling to a different commit). `tree_entry_kind` parses one raw
  `ls-tree -z` record and requires the returned path to match the query
  exactly. `path_is_current` interprets only `diff --quiet`'s 0/1 exit.

### 3. `config.py` — closed HOW-pair grammar
Added `EvidenceConfig(source, key)` and two new `JudgeConfig` fields
(`attestation_dir: str | None = None`, `evidence: tuple[EvidenceConfig, ...] | None = None`),
both defaulted so every existing `JudgeConfig(...)` construction is
unaffected. `_load_judge` now: treats `attestation_dir`/`evidence` as a
both-present-or-both-absent pair, excluded from the existing
required/surplus-rejection logic for computed rigor fields (so the pair is
legal on an R0-only lane without inventing a computed capability). Added
`_validate_attestation_dir` (canonical POSIX spelling, 1..4096 UTF-8 bytes,
≤128 components, no `..`, no control chars) and `_load_evidence` (1..64
entries, exactly `source`/`key`, `source` closed to `"attested"`, key regex
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, no duplicate `(source,key)`, order
preserved).

### 4. `attestation.py` — full rewrite of the loader/evaluator
- Closed record grammar in `parse_attestation`: exactly three keys, duplicate
  JSON member names rejected via a custom `object_pairs_hook`, every string
  UTF-8-encoded explicitly (catches a lone surrogate), fixed bounds
  (`MAX_PRODUCER_BYTES`, `MAX_REVIEWED_PATH_BYTES`, `MAX_REVIEWED_PATHS`),
  `attested_commit` matched against `[0-9a-f]{40}`, reviewed paths checked
  for canonical repo-top-relative POSIX spelling (no NUL/absolute/`.`/`..`/
  empty component) while explicitly permitting newline/backslash/leading-dash/
  pathspec-metacharacter bytes as legal filename content.
- `load_attestation_file(project_root, *, attestation_dir, key)` now reads via
  `safeio.read_bounded_input` and returns `None` for absence.
- `evaluate_attestation` rewritten against the four new `git.py` helpers:
  exact commit verification, ancestor-or-equal, every reviewed path's
  existence checked before any path's staleness is checked (so a later
  missing path always outranks an earlier stale one), then currentness.
  Catches `AssayError` and remaps to `UNREADABLE_ARTIFACT` — except
  `BUDGET_EXCEEDED/LANE_TIMEOUT`, which is explicitly re-raised unmodified.
- `load_attested_evidence` rewritten as the staged, bounded orchestrator:
  grammar checks (attestation_dir, per-declaration source/key, duplicates,
  declaration-count bound) before any I/O; stages every declared identity's
  safe read/parse in order, sampling `remaining()` after each; computes the
  aggregate query cost (`2 * sum(reviewed_paths)` across structurally valid
  records) and, if it exceeds `MAX_GIT_PATH_QUERIES`, marks every otherwise-
  valid record `UNREADABLE_ARTIFACT` and launches no Git at all for the
  batch (preserving already-known missing/malformed results); otherwise
  resolves each valid record via `evaluate_attestation` in declaration order;
  samples `remaining()` once more immediately before returning.

### 5. `measurability.py`
`check_dirty_tree`/`check_base_is_head` gained a `remaining` keyword,
forwarded to `git.repo_top`/`git.dirty_paths`/`git.head_rev`/`git.resolve_base`.

### 6. `runner.py` — one CLI-started deadline, evidence binding
- `evaluate_r1` gained `remaining`, forwarded to both measurability guards,
  `git.repo_top`, and the R1 diff's `git.run` call.
- `_coverage_artifact_is_tracked` and `_resolve_declared_base` gained
  `remaining`.
- `_execute_snapshot_unit` (the shared baseline/canary/mutation-baseline
  engine) forwards `deadline.remaining` to its tracked-artifact check and its
  post-run dirt/HEAD check.
- `_run_prepared_lane` forwards `deadline.remaining` to its R1 `evaluate_r1`
  call and its R2 diff-fallback `measurability.check_base_is_head`/`git.run`.
- `_run_higher_rigor_lane` no longer starts its own `LaneDeadline` — it now
  **requires** one as a parameter (started by the caller before HEAD), and
  forwards `remaining=deadline.remaining` to its own pre-run
  `repo_top`/`dirty_paths`/`head_rev`/`_resolve_declared_base` calls.
- Added `_lane_declared_evidence(lane)` and `_require_evidence_bound_to_lane`
  (A-213): derives the authoritative ordered evidence identities from
  `lane.judge.evidence` and requires exact ordered equality with both the
  supplied `declared_evidence` and `evidence` tuples. Called at the top of
  `run_lane`/`refuse_lane` (before any Git/plan/adapter/command work) and
  inside `assemble_verdict` (the final public sink). The old
  missing/surplus-only mutual-coverage check in `assemble_verdict` is now
  strictly implied by the new check and was **removed** (it would otherwise
  be unreachable dead code).
- `run_lane` gained `deadline: LaneDeadline | None = None` (a library
  convenience only — starts one from `lane.budget_seconds` when omitted; the
  CLI never omits it) and `evidence`/`declared_evidence` parameters, threaded
  through both the higher-rigor dispatch and the direct-R0 path. The direct-R0
  path no longer calls `execute_command`; it now resolves the plan directly
  and calls `execute_plan(..., timeout=deadline.remaining())`, so direct R0
  receives the deadline's **current remainder**, never a fresh
  `lane.budget_seconds`, after HEAD/evidence work has already spent part of
  the singular budget.

### 7. `cli.py` — the exact lifecycle
`_run_reserved` now: starts one `LaneDeadline` before HEAD; resolves HEAD with
`remaining=deadline.remaining`; converts `lane.judge.evidence` to
`EvidenceDeclaration`s; if non-empty, resolves the complete attestation batch
via `attestation.load_attested_evidence` with the same callable. On a
`LANE_TIMEOUT` from that call, builds the complete atomic-timeout refusal
(every declared rigor claim **and** every declared evidence identity as
payload-free `BUDGET_EXCEEDED/LANE_TIMEOUT`, via a new `_timed_out_evidence`
helper) and returns without ever resolving an adapter or running a command.
Otherwise resolves the adapter (adapter refusal preserves the already-resolved
evidence — never erases it) and calls `run_lane(..., evidence=..., declared_evidence=..., deadline=deadline)`.

### 8. `mutation.py` — one narrow, deliberate non-forward
`_snapshot_left_dirt` gained an optional `remaining` parameter but its **call
site inside `run_mutation` deliberately does not pass `deadline.remaining`**.
See "Controlled break / design correction" below.

### 9. `canary.py` — isolated-canary R1 forwarding
`_judge_unit` gained a required `deadline: LaneDeadline` parameter, forwarded
as `remaining=deadline.remaining` into its own `evaluate_r1` call. Both call
sites inside `run_isolated_canary` (control and transform halves) now pass
`deadline=deadline`. The legacy standalone `run_python_canary`/`_run_pipeline`
path is untouched and keeps `remaining=None`.

### 10. `tools/tester-unified-gate.sh`
Inserted, verbatim as specified, between `ASSAY_GATE_PHASE=wheel-installed`
and `run_self_hosted_lane`:
```
PYTHONPATH= ASSAY_P26_PROJECT_ROOT="$worktree/assay" \
    "$scratch/run-venv/bin/python" -m pytest \
      "$worktree/assay/nyxloom-trove/carve-assets/P26/test_acceptance.py" \
      -q -p no:randomly --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=attestation-hardened'
```
A `# shellcheck disable=SC1007` comment was added immediately above (the
intentional empty `PYTHONPATH=` assignment is SC1007's exact warning shape);
this does not change the required literal block itself, which the locked
`test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it`
verifies as an exact substring.

## Controlled break / design correction (mutation.py)

While threading `remaining` through every lane-owned Git call, the pre-
existing test `tests/test_runner_p23_cleanup_and_budget.py::test_every_identity_after_an_expiry_is_budget_stopped_not_only_the_next`
broke when `_snapshot_left_dirt`'s post-run dirt/HEAD check was given
`remaining=deadline.remaining`. Root cause: `run_mutation`'s own docstring and
this test both establish "expiry marks a NOT-YET-STARTED identity
`budget_exceeded`; a completed identity's result remains evidence, never
discarded for a partial sample." `_snapshot_left_dirt` runs strictly *after*
a mutant's process has already produced a decisive result — forwarding the
shared deadline there let an expiry discovered only at that bookkeeping step
(not before the mutant's own `materialize`/`execute_plan` budget samples)
retroactively reclassify an already-completed, already-decisive "killed"
mutant as `budget_exceeded`, silently discarding real evidence and violating
AUTHORING §3b.A ("nothing may make the verdict depend on how fast the machine
is" — here, on exactly when the shared clock crosses the deadline relative to
a bookkeeping check that has no bearing on the mutant's own already-obtained
result). Budget enforcement for *not-yet-started* work already happens at
`materialize_timeout = deadline.remaining()` and `execute_plan`'s own
`timeout=deadline.remaining()` sample, both **before** the process runs. The
fix: `_snapshot_left_dirt`'s call site inside `run_mutation` deliberately
omits `remaining`, so its Git calls verify structural integrity unconditionally
(never budget-gated) once a mutant has already run. This reproduces the exact
pre-existing killed-count assertion and is documented at both the function's
docstring and its one call site. No locked P26 test exercises this specific
call site, so this is a pure design correction against the ordinary suite's
existing, well-reasoned contract — not a contract violation of the frozen
handoff (which states "change no bucket semantics" for this exact call).

## Test changes

Four existing attestation test files were rewritten (not merely patched) to
match the new API — the pre-P26 versions imported now-removed private helpers
(`_check_ancestor_or_equal`, `_check_reviewed_paths_exist`) and called
now-changed signatures (`load_attestation_file(path)`,
`load_attested_evidence(..., attestations_dir=Path)`,
`evaluate_attestation(...)` without `remaining`). Every O1/O2/O3 behavioral
claim from the original files is preserved verbatim in the rewrite; only the
call shape changed. `tests/test_attestation_record.py`,
`tests/test_attestation_evaluate.py`, `tests/test_attestation_load_declared.py`,
`tests/test_attestation_pipeline_integration.py` — full rewrites.

`tests/test_runner_assemble_verdict_evidence.py` — the new A-213 binding
check means a lane must declare `judge.evidence` matching whatever
`evidence`/`declared_evidence` a caller passes to `assemble_verdict`. Added
`_r0_pass_result_declaring_review` (a lane fixture declaring
`evidence=[("attested","review")]`) and used it for the four tests that pass
non-empty evidence; the surplus-evidence and omit-entirely tests already used
a lane declaring none, which is the correct fixture for testing evidence
resolved-but-never-declared against an empty authoritative source, so those
two were left unchanged.

`tests/test_git_boundary.py`,
`tests/test_runner_run_lane_r2.py` — two pre-existing tests monkeypatch
`git._run_bounded`/`git.run` with a fake lacking the new `remaining` keyword;
both fakes were updated to accept and forward it (`fake_bounded(argv, *, remaining=None)`,
`counting_run(repo, *args, **kwargs)`). Pure signature-compatibility fixes;
no assertion was weakened.

## Verification

Commands run from `assay/`, in order:

```text
PYTHONPATH=src python -m pytest nyxloom-trove/carve-assets/P26/test_acceptance.py -q -p no:randomly --tb=no
```
Result: **41 passed** (up from the 9/32 baseline).

```text
PYTHONPATH=src python -m pytest tests/ -q -p no:randomly
```
Result: **2306 passed, 10 skipped** (identical skip count to pre-implementation;
0 failures).

```text
PYTHONPATH=src python -m pytest tests/ nyxloom-trove/carve-assets/P26/test_acceptance.py -q -n 4
```
Result: **2347 passed, 10 skipped** under `pytest-xdist` 4-way parallel —
identical pass count to the serial run, confirming no order/worker pollution.
(`pytest-randomly` is not installed in this environment; `-p no:randomly` is
a harmless no-op flag either way, matching the locked suite's own invocation.)

Ruff: `ruff check` against every touched `src/assay/*.py` file produces the
**same finding count as the pre-P26 baseline** (verified via `git stash`/
`git stash pop` diffing before/after), except `canary.py`, which gains exactly
one additional `F821 Undefined name 'LaneDeadline'` — the same
already-tolerated pattern this file already has three instances of (a bare
annotation name under `from __future__ import annotations`, never evaluated
at runtime, already present for the module's other `deadline: LaneDeadline`
parameters). No new ruff *category* was introduced; this project's registered
gate does not run ruff (`nyxloom-trove/nyxloom.toml`'s `[gates.tester-unified]`
declares `asserts = ["tests-pass"]` only).

`nyxloom lint`: zero findings for any `assay-P2*` handoff/asset.

`git diff --check`: clean (no whitespace errors).

Locked-asset re-verification after implementation:
```text
ALL 11 HASHES OK
manifest self-hash: 4cb702ddad368becd8aca55c0d5ef6ac2c55a086bb88751ff7a450d3b05352f8
```
Identical to the pre-implementation values. No locked asset was edited.

Premise probe: intentionally **not** re-run after implementation. It calls
the pre-P26 signatures directly (`evaluate_attestation(...)` without
`remaining`, `load_attested_evidence(..., attestations_dir=Path)`) to
demonstrate the OLD implementation's bugs; the frozen contract (locked
`interface-contract.json`) requires changing exactly those signatures, so the
probe cannot execute against the new API by design — this is the intended
outcome of implementing the contract, not a regression. Its hash is verified
unchanged above, and its pre-implementation PASS record is reproduced
verbatim in "Pre-implementation verification".

## Self-review checklist (per dispatch instructions)

- **Scope**: `git diff --stat` against `verdict.py`, `schemas/`,
  `isolation.py`, `adapters/`, `gate/`, the handoff, the JIT report, and
  `nyxloom-trove/carve-assets/P26/` all return empty — none were touched.
- **Reachability**: the superseded missing/surplus evidence check in
  `assemble_verdict` was removed rather than left as dead code once the new
  binding check made it structurally unreachable.
- **Safe I/O**: `read_bounded_input` walks every component via `dir_fd` +
  `O_NOFOLLOW`, never a whole-path open; ENOENT-at-any-component is `None`,
  every other failure is `UNREADABLE_ARTIFACT`.
- **Literal path handling**: the four new Git helpers share `_run_raw`, which
  shares the exact same argv construction (including the mandatory
  `--literal-pathspecs` global option) as every other command in `git.py`;
  no bespoke child bypasses it.
- **Process-group cleanup**: `_kill_owned_group` is called unconditionally on
  any non-completed path, independent of `proc.poll()` — verified directly by
  the locked `test_generic_git_expiry_kills_the_complete_group_and_preserves_the_terminal`
  and `test_generic_git_overflow_also_kills_its_complete_process_group`.
- **Deadline forwarding**: verified end-to-end by the locked suite
  (`test_measurability_forwards_one_remaining_owner`,
  `test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget`,
  `test_bootstrap_timeout_launches_no_substantive_git_command`) plus the
  canary.py gap found and closed during self-review (see above).
- **Identity binding**: `_require_evidence_bound_to_lane` verified by the
  locked `test_runner_binds_evidence_batch_to_lane_source_before_any_work`.
- **Duplicate/Unicode JSON rejection**: `_no_duplicate_pairs` (custom
  `object_pairs_hook`) and `_utf8_bytes` (explicit `.encode("utf-8")`, catches
  a lone surrogate) verified by the locked
  `test_record_grammar_rejects_duplicate_json_members_before_git` and the
  `producer: "\ud800"` mutation case in
  `test_record_grammar_is_closed_and_bounded_before_git`.
- **Gate installed-wheel ownership**: the inserted block runs
  `"$scratch/run-venv/bin/python"` (the wheel-just-installed interpreter) with
  `PYTHONPATH=` cleared and `--override-ini=pythonpath=`, so `pyproject.toml`'s
  `src/` cannot shadow the installed wheel — verified by the locked
  `test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it`.

## Successor briefs

- **P27** (Go gate/adapter resolution): unaffected by this package. P26 does
  not register Go and does not touch adapter-refusal semantics beyond
  preserving already-resolved evidence on refusal (verified by
  `test_cli_preserves_independent_malformed_missing_and_current_evidence`'s
  shape, and the CLI's own `_resolve_declared_adapters` exception handler,
  which now threads `evidence`/`declared_evidence` through unchanged).
- **P29/P30** (symbolic `judge.base`, reachability sweep): P26's full-OID
  requirement (`verify_exact_commit`) applies only to untrusted attestation
  identity; `judge.base`'s own resolution path
  (`runner._resolve_declared_base` → `git.resolve_base`) is unchanged in
  shape, only gained `remaining` forwarding.
- No other open items. The controller owns the registered outer gate run,
  serial merge, post-merge locked-suite re-verification, and the P27 route
  packet.

## Commit

One clean implementation commit follows this LOG, containing exactly the
files listed under "Implementation" and this LOG file itself.
