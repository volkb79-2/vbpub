# Design: declared release preflight (P-core-13)

Scope: KI-190, which also closes KI-183. One package and one merge, after P-core-06 (P7).
Related: KI-182 (DinD row), KI-191 (tester-gate), KI-194/P-core-12, KI-141 part 7 (T3).

## Decisions

1. **`preflight` is a reserved, optional step name and never a pipeline phase; template
   revision stays 5.** An optional step needs no new grammar table (as in P7 D7).
2. **No new step keys: the timeout is the constant `PREFLIGHT_TIMEOUT_SECONDS = 600`.** The
   older installed engine parses the same `cmru.toml` during a self-release and refuses unknown
   keys with exit 2.
3. **It runs before dispatch and workspace creation, for every project in scope that has a
   planned release.** A refusal then leaves no candidate to retire (KI-179), and the scope no
   longer decides whether it runs (KI-183).
4. **The command runs in a `git archive` export of the release-source revision, one per Git
   family.** It judges the exact commit, as the exec-from-blob did, with no worktree, hooks or
   `.git`, and without generic code knowing which files it reads.
5. **stdout is one JSON document (`format` 1), not JSON Lines.** A truncated document is
   invalid, while a crashed line stream leaves valid-looking rows.
6. **Statuses are exactly `PASS`, `FAIL` and `SKIPPED`; there is no `WARN`.** `GatePrecondition`
   already renders these three. A non-blocking concern is `SKIPPED` with a remedy, like
   today's dry-run wheel-builder row.
7. **Fail closed: a crash or malformed output becomes one synthesized `FAIL` row, and no regex is
   applied to any output.** stderr is shown, never parsed.
8. **A FAIL refuses with exit 4 (`REFUSED`) before any change, as the KI-182 rows do.** All rows
   are collected first and reported together. Grammar errors exit 2; an unreadable revision or
   a failed export exits 1.
9. **Generic cmru resolves `argv[0]` on PATH like any step. cmru declares `python3 -I -B`, not
   the plan's `python`.** `python` is absent on Debian hosts, and `-I -B` matches the `gate`
   lanes. The script re-execs once under `CMRU_INTERNAL_PYTHON`.
10. **The env is the ordinary step env, minus publisher credentials, plus two protected
    names.** Code that no gate has judged must not hold publish rights. Its GitHub reads are
    unauthenticated (`tool_deps._github_get_json`).
11. **Dry-run runs it. The command reads `CMRU_PREFLIGHT_MODE` and chooses `SKIPPED` or `FAIL`
    itself; cmru never rewrites a status.** "Docker unavailable" stays SKIPPED on a dry-run and
    FAIL on a release, as KI-182 does.
12. **`--resume` never runs it. `--skip-gates` yields a `SKIPPED` row without running it.** On
    resume the change already exists, and skipping the gates skipped the identity row before too.
13. **The image rows stay built in, keyed on cmru's own verbs and never on project names.** Each
    is checked against the project's own config: wheel-builder for argv `cmru handler
    wheel-build`, cgroup probe for `cmru tester-gate`, DinD for `--enable-docker` (KI-182). These
    rows guard handlers that cmru ships, which closes KI-183. The probe and DinD rows leave with
    tester-gate (KI-191). cmru loses its probe row because its gate never starts the probe.
14. **The Run-Gate identity row moves into `cmru/tools/run_release_gate.py --preflight --json`.
    The code that raises an error attaches its remedy.** Each regex branch at
    `release_preconditions.py:186-227` becomes `GatePreflightError(detail, remedy)`, a
    `RuntimeError` subclass. Docker availability is the exit status of `docker version`.
15. **`_current_run_gate_identity` keeps its name and signature.** Installed engines older than
    this release still exec the script and call it, and the gate still uses it.
16. **The `docs/BOOTSTRAP-FROM-ZERO.md` literal check moves to a `libraries/test-policy` estate
    lane, not to a cmru docs test.** That root document covers the whole estate (L0-L4), so cmru
    owns none of it (KI-194). The check is pure text, and the move ends the "engine-too-old"
    false FAIL.
17. **`gate_snapshot` gets its closeout lanes from the `assay_lane` values in `cmru/run-gate.toml`
    for the lanes `_plan_gate` can select.** When KI-141 part 7 deletes a lane, the list shrinks
    with no code edit.
18. **The generic runner lives in `release_preconditions.py`.** It is already a `cmru/assay.toml`
    target, so no T3-owned edit is needed.

## Contract

**Grammar.**

```toml
[steps.preflight]
quiet = true
commands = [{ label = "cmru: release preflight", argv = ["python3", "-I", "-B", "tools/run_release_gate.py", "--preflight", "--json"], cwd = "." }]
```

- **Allowed keys.** `_validate_runner_steps` requires exactly one command and allows only
  `commands`, `quiet` and `env`. Any other key, including P7's `no_progress_timeout_seconds`,
  exits 2: `steps.preflight only permits commands (exactly one), quiet and env; got <key>`.
- **Reserved name.** `record_step` (`config.py:658`), `build_step` and `default_steps` refuse
  `preflight`.

**When.**

- **Where.** In `_release_launcher`, after the origin/feature source preflight and before
  `_dispatch_independent_git_families`. It replaces both blocks at `cli.py:7490-7514`.
- **Per Git family.** cmru resolves the revision (`fetch_origin_main` or the `--from-worktree`
  head) and runs `status_records` at it for every project in scope. A project with nothing to
  release gets `SKIPPED "no planned release"`. Otherwise the export is created on first use, and
  the step and `[env]` are parsed from the export's config, not from the caller checkout.
- **Multi-family, pinning, never.** The parent checks every family quietly; each dispatched child
  re-checks and reports its own (today's `report=` rule). The judged revision becomes the
  per-family `cmru_precondition_base`; a later mismatch refuses with
  `_release_source_changed_retry_message`. Never on `--resume` or inside a transaction child.

**Execution.**

- **Call and cwd.** `run_process(capture=True, stdin=DEVNULL, timeout=600)` (P7's
  `procio.run_capture` delegation; a timeout reaps the group), in
  `<export>/<project dir>/<command.cwd>` via `resolve_cwd`.
- **Environment.** The `run_project_step` builder (factored out of `cli.py:336`, not copied):
  the export's project `[env]` over its orchestration defaults, the bound `cmru` launcher,
  `CMRU_INTERNAL_PYTHON`, `PYTHONUNBUFFERED=1`, a private `TMPDIR`. Removed: every
  `config._RESERVED_CREDENTIAL_ENV` name, `CMRU_RELEASE_*`, `CMRU_RELEASE_GATE_*`, the record
  keys. Added as protected names (refused in `[env]`, cleared on every other step):
  `CMRU_PREFLIGHT_MODE=dry-run|release` and `CMRU_PREFLIGHT_COMMIT=<sha>`.
- **Cleanup.** The command must not write into the export, which is removed in a `finally`.

**Row document.** stdout must be strict UTF-8 and at most 1 MiB, for example:

```json
{"format": 1, "rows": [{"check": "Run-Gate image identity", "status": "FAIL", "detail": "...", "remedy": "..."}]}
```

- **Shape.** The document has exactly `format` (the integer 1) and `rows` (non-empty). Each row
  has exactly `check`, `status`, `detail`, `remedy`, all strings; `check` non-empty, `status`
  one of the three, `remedy` non-empty on a FAIL. Parsed by `json.loads` with an
  `object_pairs_hook` that refuses duplicate keys.
- **Output.** cmru adds the `project` column and renders with `format_gate_precondition_report`;
  full stdout and stderr go to `cmru.release.log`. Exit 0 makes the document authoritative,
  whatever its rows say.

**Synthesized FAIL rows.** Each is one row with `check = "preflight"`, and the release refuses
with exit 4:

| Cause | detail | remedy |
|---|---|---|
| spawn error | `cannot start <argv[0]>: <OSError>` | fix `steps.preflight` argv or install the tool |
| timeout | `did not finish within 600 s; process group reaped` | find the hang (stderr tail shown) |
| exit N != 0 | `exited N` and the last 20 stderr lines | fix the command (any parsed rows also listed) |
| bad output | `malformed preflight output: <json path>: <reason>` | emit format 1 per this note |

`<reason>` is one of: not UTF-8, over 1 MiB, not JSON, a duplicate, missing or extra key, the
wrong type, empty `rows`, an unknown status, a FAIL without a remedy.

**cmru's `--preflight` mode.**

`--worktree` defaults to the script's repository root (the export); `--json` prints the
document, otherwise a human table. Exit 0 once the document is written, 1 with
`cmru-release-gate: ...` if the script itself breaks. `_authoritative_interpreter` honours
`CMRU_INTERNAL_PYTHON` together with `CMRU_PREFLIGHT_COMMIT`. One row: `Run-Gate image identity`.

## What is deleted or moved

- **`release_preconditions.py`.**
  - Moved to the test-policy lane (D16): `:25-41` (bootstrap sequence and patterns) and
    `:430-489` (bootstrap functions).
  - Deleted: `:84-91` `_load_release_gate` (the exec); `:99-119`
    `_selected_tester_image_from_source` (names `tester-unified`, `run-gate.root.toml`);
    `:122-157` `_materialize_gate_identity_config`; `:160-235`
    `_run_gate_release_identity_preflight` (regex remedies); `:238-255`
    `check_run_gate_image_precondition`; `:492-514` `evaluate_release_gate_preconditions` (keys
    on `"cmru"`); `:69-81` `_git_blob_text`, left without a caller (KI-189); imports `re`,
    `tempfile`, `ModuleType`.
- **`cli.py`.**
  - `:7154-7253` `_release_gate_preconditions_before_dispatch` and `:7331-7402`
    `_release_dind_image_preconditions` become one generic per-family function; the build-image
    block `:7208-7224` becomes the keyed rows (D13); the `distinguish = name != "cmru"` rule
    (`:7382`) goes, since the project column names the project.
  - Deleted: `:7321-7328` `_cmru_snapshot_config_text` and `:7405-7443`
    `_project_config_for_release_snapshot`, which hard-codes `name="cmru"`.
  - Made per family: `cmru_precondition_base` at `:7458`, `:7490-7514`, `:7583-7588` and
    `:7716-7722`.
- **`gate_snapshot.py:25-56`.** `_ASSAY_CLOSEOUT_LANES`, `_LANE_EVIDENCE_FILES` and
  `_EVIDENCE_SET` become `GateSnapshot` fields set by `open_gate_snapshot(...,
  closeout_lanes=)`. `:960` `_existing_evidence_hashes`, `:1012` `copy_evidence_to_source` and
  `:1065` `validate_lane_outputs` receive the set.
- **`tools/run_release_gate.py`.** `main` (`:1540-1558`) gains `--preflight`/`--json`; `:415`
  `_check_run_gate_identity`, `:618` `_check_run_gate_release_artifacts` and `:722`
  `_check_tester_image_identity` raise `GatePreflightError`; `_plan_gate` (`:1420`) supplies the
  closeout lanes.
- **`cmru/cmru.toml`.** Gains `[steps.preflight]`.
- **Tests.** `tests/test_release_gate_preconditions.py` is rewritten to this contract under the
  same name (the `release-preconditions` lane is untouched); the `_git_blob_text` cases in
  `tests/test_r2_survivors_s1.py:326-341` go; `tests/test_bootstrap_from_zero_sequence.py:25`'s
  import follows the move.
- **Docs.** SPEC S-REL.7 (`SPEC.md:1677`), README, CONSUMERS and CHANGES are updated.
  `RELEASE-TRANSACTIONS.md:84-92` ("engine-too-old") is deleted.

## Acceptance (behavioural, fake boundary)

**Fixture.** A temporary Git estate with projects `alpha` and `beta`, neither named cmru. It has
a fake `docker`, a fake `run-gate` and a fake GitHub transport. The preflight scripts record
their argv, cwd and env outside the export.

1. `cmru release alpha --dry-run` (cmru not in scope) runs alpha's rows. A FAIL exits 4 and
   prints its remedy, and no `cmru-release-*` ref, worktree or dispatch follows. With all rows
   PASS, the dry-run reaches the plan.
2. Each synthesized cause exits 4 naming its reason: spawn error, exit 3, truncated JSON, a
   duplicate key, an extra key, an unknown status, a FAIL without a remedy, non-UTF-8 output,
   more than 1 MiB. The timeout case uses a pipe-blocked child under
   `requires_contained_cgroup` (TP-13).
3. The recorded env has no credential names, `CMRU_PREFLIGHT_COMMIT` equals the origin revision
   and `CMRU_PREFLIGHT_MODE` is `dry-run` or `release`. The cwd is inside an export with no
   `.git`, and the export is gone afterwards.
4. The step comes from the revision. Origin declares a failing preflight and the caller is one
   commit behind without it; the release still refuses.
5. `--resume`, `--skip-gates` and an unchanged project run no command. The last two show
   `SKIPPED`.
6. Each of these exits 2: two commands, `login`, `no_progress_timeout_seconds`,
   `build_step = "preflight"`, and `preflight` in `default_steps`.
7. KI-183: a non-cmru project with `cmru handler wheel-build` gets the wheel-builder row, and one
   with `cmru tester-gate` gets the probe row.
8. `run_release_gate.py --preflight --json`: each former regex branch yields its remedy from the
   raise site. Docker unavailable is SKIPPED in `dry-run` mode and FAIL in `release` mode.
9. Deleting `[lanes.assay-first-release]` from a fixture `run-gate.toml` drops
   `campaign-cmru-first-release.*` from the evidence set, with no code change.
10. An unchanged revision-5 project without the step loads, passes `cmru standards` and gets
    only the built-in rows.

Before merge, the main controller runs one real `cmru release cmru --dry-run` and one
`cmru release ciu --dry-run`, both with the engine built from the branch head.

## Migration order (no release runs without the checks)

- **M0 (P-core-12).** The test-policy estate lane holds the bootstrap literal check and is green.
  The engine still runs its own copy.
- **M1 (this branch, one merge; origin/main sees only the merge commit).** Commits: (1)
  `--preflight --json` and its tests; (2) the generic runner, grammar and keyed rows, old path
  still in place; (3) one swap commit: `cmru.toml` declares the step, and the exec, regex and
  bootstrap code and their docs go; (4) the `gate_snapshot` lanes from config.
- **Version skew.** An older installed engine still execs the unchanged
  `_current_run_gate_identity` (D15), checks the unchanged bootstrap document, and ignores
  `[steps.preflight]`, which is neither a phase nor a new key (D2). A `--from-worktree` branch
  based before M1 loses only the fast fail: `release-gate` re-checks the identity at gate time
  (`run_release_gate.py:1327`).
- **M2.** cmru releases itself with the merge-head engine (the RELEASE-TRANSACTIONS self-release
  recipe), after the M1 dry-runs.

## Out of scope

A `cmru preflight` verb; a `WARN` status; rows kept in transaction state for `--resume`; a
Run-Gate-owned identity preflight for every run-gate-gated project (run-gate backlog);
`gate_snapshot`'s other `cmru/...` evidence paths and moving it into `cmru/tools`;
`_is_docker_unavailable` in the built-in rows; removing the probe and DinD rows (KI-191); the
bootstrap-order test move and the estate lane itself (P-core-12); KI-141 part 7, `cmru/assay.toml`
edits, a template revision bump, scaffold examples.

## Open coordination items

1. **T3 (vbpub-assay)** confirms:
   - `campaign-<assay_lane>.{exit,json,txt,err}` is Assay's stable closeout naming;
   - `cmru-first-release-r2` writes no closeout files into the release gate;
   - whether KI-141 part 7 lands first (either order works, but then the fixtures must not name
     the first-release lane);
   - that neither the smaller `release_preconditions.py` nor the deleted survivor tests need a
     `cmru/assay.toml` change.
2. **The main controller:**
   - sequences P-core-13 after P-core-06 and after M0;
   - adds the test-policy estate lane to the gate set of any merge that touches root `docs/`,
     because "gate every project dir a merge touches" does not reach root docs;
   - runs the two real dry-runs.
3. **The operator** confirms `python3 -I -B` over the plan's `python` (D9), and dropping cmru's
   own probe row (D13).
