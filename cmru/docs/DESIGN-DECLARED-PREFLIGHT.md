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
   `.git`, and without generic code knowing which files it reads. It lives in
   `tempfile.mkdtemp(prefix="cmru-preflight-")` under the default temp dir and costs about
   204 MiB and 9.9k files for vbpub today. A multi-family release exports each family twice
   (parent and child). cmru has no SIGTERM handler, so the `finally` does not run on SIGTERM.
   Each start therefore sweeps the user's `cmru-preflight-*` dirs older than 24 h; until the
   next sweep, a killed run's export stays behind (a known issue).
5. **stdout is one JSON document (`format` 1), not JSON Lines.** A truncated document is
   invalid, while a crashed line stream leaves valid-looking rows.
6. **Statuses are exactly `PASS`, `FAIL` and `SKIPPED`; there is no `WARN`.** `GatePrecondition`
   already renders these three. A non-blocking concern is `SKIPPED` with a remedy, like
   today's dry-run wheel-builder row. `format_gate_precondition_report` therefore prints the
   remedy of every row whose remedy is non-empty, not only FAIL rows, so today's dry-run
   `docker pull` hint becomes visible.
7. **Fail closed: a crash or malformed output becomes one synthesized `FAIL` row, and no regex is
   applied to any output.** stderr is shown, never parsed.
8. **A FAIL refuses with exit 4 (`REFUSED`) before any change, as the KI-182 rows do.** All rows
   are collected first and reported together. Grammar errors exit 2; an unreadable revision or
   a failed export exits 1.
9. **Generic cmru resolves `argv[0]` on PATH like any step. cmru declares `python3 -I -B`, not
   the plan's `python`.** `python` is absent on Debian hosts, and `-I -B` matches the `gate`
   lanes. The script re-execs once under `CMRU_INTERNAL_PYTHON`.
10. **The env is the ordinary step env, minus credential variables, plus two protected
    names.** Code that no gate has judged gets no token in its environment. This is a reduction,
    not a sandbox: credentials under HOME (gh config, docker config, git credential helpers)
    stay reachable. Its GitHub reads are unauthenticated (`tool_deps._github_get_json`).
    `cmru run <p> --step preflight` exits 2 ("preflight runs only inside `cmru release`"), so no
    second environment exists. For a manual run, call the command directly.
11. **Dry-run runs it. The command reads `CMRU_PREFLIGHT_MODE` and chooses `SKIPPED` or `FAIL`
    itself; cmru never rewrites a status.** "Docker unavailable" stays SKIPPED on a dry-run and
    FAIL on a release, as KI-182 does.
12. **`--resume` never runs it. `--skip-gates` yields a `SKIPPED` row without running the
    command, while the built-in image rows still run.** On resume the change already exists.
    Skipping the gates already skipped the identity row, and the image rows already ran first
    (`cli.py:7208-7224`, before the skip branch); build and publish still need those images.
13. **The image rows stay built in, keyed on cmru's own verbs and never on project names.** Each
    is checked against the project's own config. The wheel-builder row applies to an argv that
    starts `cmru handler wheel-build`. The cgroup-probe row applies to `cmru handler wheel-build`
    OR `cmru tester-gate`, because wheel-build starts the probe with `--pull=never`
    (`handlers.py:651` `require_installed_cgroup_parent` → `tester_gate.check_slice_unit` →
    `_run_probe`, `tester_gate.py:294`). The DinD row applies to `--enable-docker` (KI-182).
    These rows guard handlers that cmru ships, which closes KI-183. All 16 wheel-build projects,
    cmru included, keep a probe row. Only the DinD row leaves with tester-gate (KI-191).
    The rows run for every project in scope, whether or not it has a planned release (today's
    DinD rule). SPEC S-REL.7's "omit the row (not report it `SKIPPED`) for a project with no
    such step" (`SPEC.md:1704`) holds per row: no matching argv, no row.
14. **The Run-Gate identity row moves into `cmru/tools/run_release_gate.py --preflight --json`.
    The code that raises an error attaches its remedy.** Each regex branch at
    `release_preconditions.py:186-227` becomes `GatePreflightError(detail, remedy)`, a
    `RuntimeError` subclass. Docker availability is the exit status of `docker version`.
15. **`_current_run_gate_identity` keeps its name and signature.** Installed engines older than
    this release still exec the script and call it, and the gate still uses it.
16. **The `docs/BOOTSTRAP-FROM-ZERO.md` literal check moves to the `estate-lint` project
    (`estate-lint/suite`), not to a cmru docs test.** (P-core-12 amendment: the layout is
    `estate-lint/`, see KI-194; the `libraries/test-policy` estate lane named in the original
    decision was rejected.) That root document covers the whole estate (L0-L4), so cmru
    owns none of it (KI-194). The check is pure text, and the move ends the "engine-too-old"
    false FAIL.
17. **`gate_snapshot` gets its closeout lanes from the installed run-gate CLI: the `assay_lane`
    of each lane `_plan_gate` can select (now only `assay`), read from
    `run-gate --list --json` (R-55, `run-gate-list/1`) in `cmru/`.** `cmru/run-gate.toml` is
    "parsed ONLY by the installed run-gate CLI" (D-110), so a second parser is out. A named lane
    that is missing or is not `kind = "assay"` refuses the gate; the list never silently
    shrinks. KI-141 part 7 has landed: `_plan_gate` selects only `assay`, so the closeout
    set is `("cmru",)`.
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
- **Per Git family.** The revision is the parent's `origin_main_snapshots` entry for a
  non-dry-run multi-family release (no second fetch), the feature head with `--from-worktree`,
  and `fetch_origin_main` otherwise. `status_records` at that revision covers every project in
  scope. A project with nothing to release gets `SKIPPED "no planned release"`. Otherwise the
  export is created on first use, and the step and `[env]` come from the export's config.
- **Multi-family, pinning, never.** The parent checks every family quietly; each dispatched child
  re-checks and reports its own (today's `report=` rule). The judged revision becomes the
  per-family `cmru_precondition_base`. If origin moves during the preflight (which can take up to
  600 s), the launcher's re-fetch (`cli.py:7589-7599`, `:7716-7722`) refuses with
  `_release_source_changed_retry_message` and exit 4, before any workspace exists. A FAIL in any
  family stops every family's dispatch. Never on `--resume` or inside a transaction child.

**Execution.**

- **Call and cwd.** `run_process(capture=True, stdin=DEVNULL, timeout=600)` (P7's
  `procio.run_capture` delegation; a timeout reaps the group), in
  `<export>/<project dir>/<command.cwd>` via `resolve_cwd`.
- **Environment.** The `run_project_step` builder (factored out of `cli.py:336`, not copied):
  the export's project `[env]` over its orchestration defaults, the bound `cmru` launcher,
  `CMRU_INTERNAL_PYTHON`, `PYTHONUNBUFFERED=1`, a private `TMPDIR`. Removed: every
  `config._RESERVED_CREDENTIAL_ENV` name, `_AMBIENT_GH_TOKEN_KEYS` (`cli.py:333`: `GH_TOKEN`,
  `GH_ENTERPRISE_TOKEN`, `GITHUB_TOKEN`), `CMRU_RELEASE_*`, `CMRU_RELEASE_GATE_*`, the record
  keys. Added as protected names (refused in `[env]`, cleared on every other step):
  `CMRU_PREFLIGHT_MODE=dry-run|release` and `CMRU_PREFLIGHT_COMMIT=<sha>`.
- **Cleanup.** The command must not write into the export, which is removed in a `finally`.

**Row document.** stdout must be strict UTF-8 and at most 1 MiB, for example:

```json
{"format": 1, "rows": [{"check": "Run-Gate image identity", "status": "FAIL", "detail": "...", "remedy": "..."}]}
```

- **Shape.** The document has exactly `format` (the integer 1) and `rows` (non-empty). Each row
  has exactly `check`, `status`, `detail`, `remedy`, all strings; `check` non-empty and unique
  within the document, `status` one of the three, `remedy` non-empty on a FAIL. It is parsed by
  `json.loads` with an `object_pairs_hook` that refuses duplicate keys.
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
wrong type, empty `rows`, a duplicate `check`, an unknown status, a FAIL without a remedy.

**cmru's `--preflight` mode.**

`--worktree` defaults to the script's repository root (the export); `--json` prints the
document, otherwise a human table. Exit 0 once the document is written, 1 with
`cmru-release-gate: ...` if the script itself breaks. `_authoritative_interpreter` honours
`CMRU_INTERNAL_PYTHON` together with `CMRU_PREFLIGHT_COMMIT`. One row: `Run-Gate image identity`.
The mode calls `_load_components()`, importing cmru, assay and worktree from the export's own
source roots, so a host `python3` with cli-extended but no cmru still works; without that call,
`ModuleNotFoundError` would refuse every cmru release. It skips `_assert_no_untracked_gate_code`
and `_preflight_assay_source` (the export has no `.git`); the gate still runs both.

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
  - The remaining `"cmru"` literals go: the default `project="cmru"` (`:289`) and the
    hard-coded project of every `_check_local_image_precondition` row (`:337-424`). Both take a
    required `project`, and `check_release_build_images_precondition` passes it through.
  - `format_gate_precondition_report` (`:517-524`) prints any non-empty remedy (D6).
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
  closeout_lanes=)`. The set reaches `:618` (`_verify_source`, the tracked-evidence refusal),
  `:960` `_existing_evidence_hashes`, `:1012` `copy_evidence_to_source` and `:1116`
  (`validate_lane_outputs`). `:1018` `copy_evidence_to_snapshot` reads only the manifest and
  `_MUTATION_RESUME_FILES` and is unchanged.
- **`tools/run_release_gate.py`.** `main` (`:1540-1558`) gains `--preflight`/`--json`; `:415`
  `_check_run_gate_identity`, `:618` `_check_run_gate_release_artifacts` and `:722`
  `_check_tester_image_identity` raise `GatePreflightError`. The closeout lanes come from
  `run-gate --list --json` (D17), called after the identity check (`:1327`), and are passed to
  `open_gate_snapshot` (`:1341`).
- **`cmru/cmru.toml`.** Gains `[steps.preflight]`.
- **Tests.** `tests/test_release_gate_preconditions.py` is rewritten to this contract under the
  same name (the `release-preconditions` lane is untouched). These cases in
  `tests/test_r2_survivors_s1.py` are deleted (T3 must know): `:326-341` (`_git_blob_text`),
  `:354-385` (`_materialize_gate_identity_config`) and `:388-480`
  (`_run_gate_release_identity_preflight`). The import at
  `tests/test_bootstrap_from_zero_sequence.py:26` follows the move.
- **Docs.** SPEC S-REL.7 (`SPEC.md:1677`), README, CONSUMERS and CHANGES are updated. M1
  qualifies `RELEASE-TRANSACTIONS.md:84-92` ("engine-too-old") as applying to engines older
  than the M1 release; M2 deletes it.

## Acceptance (behavioural, fake boundary)

**Fixture.** A temporary Git estate with projects `alpha` and `beta`, neither named cmru. It has
a fake `docker`, a fake `run-gate` and a fake GitHub transport. The preflight scripts record
their argv, cwd and env outside the export.

1. `cmru release alpha --dry-run` (cmru not in scope) runs alpha's rows. A FAIL exits 4 and
   prints its remedy, and no `cmru-release-*` ref, worktree or dispatch follows. With all rows
   PASS, the dry-run reaches the plan.
2. Each synthesized cause exits 4 naming its reason: spawn error, exit 3, truncated JSON, a
   duplicate key, an extra key, two rows with the same `check`, an unknown status, a FAIL without
   a remedy, non-UTF-8 output, more than 1 MiB. The timeout case uses a pipe-blocked child under
   `requires_contained_cgroup` (TP-13).
3. The recorded env lacks `GH_TOKEN` and `GITHUB_PUSH_PAT`, although the caller set both.
   `CMRU_PREFLIGHT_COMMIT` is the origin revision and `CMRU_PREFLIGHT_MODE` is `dry-run` or
   `release`. The cwd is in an export with no `.git`, and the export is gone afterwards.
4. The step comes from the revision. Origin declares a failing preflight and the caller is one
   commit behind without it; the release still refuses.
5. `--resume`, `--skip-gates` and an unchanged project run no command; the last two show
   `SKIPPED`. Under `--skip-gates` a missing probe image still exits 4 through its built-in row.
6. Each exits 2: two commands, `login`, `no_progress_timeout_seconds`, `build_step =
   "preflight"`, `preflight` in `default_steps`, `cmru run alpha --step preflight`.
7. KI-183, for non-cmru projects. `cmru handler wheel-build` gives the wheel-builder and probe
   rows, and with the probe image absent `cmru release <p>` exits 4 before dispatch.
   `cmru tester-gate` gives the probe row. An unplanned in-scope project with `--enable-docker`
   still gets its DinD row.
8. `run_release_gate.py --preflight --json`: each former regex branch yields its remedy from the
   raise site. Docker unavailable is SKIPPED in `dry-run` mode and FAIL in `release` mode.
9. The gate snapshot, with a fake `run-gate --list --json`. If the inventory lacks
   `assay`, the gate refuses before any lane runs. If it maps that lane to
   `cmru-fr2`, `validate_lane_outputs` refuses a lane that leaves the formerly hard-coded
   `cmru/.assay/campaign-cmru.json` as unapproved output (proving the set is derived from the
   inventory, not from `_ASSAY_CLOSEOUT_LANES`), and `campaign-cmru-fr2.json` is copied back.
10. An unchanged revision-5 project without the step loads, passes `cmru standards` and gets
    only the built-in rows.
11. A multi-family release where family B's preflight FAILs: exit 4, and no family dispatches,
    including family A, which passed.
12. With `--from-worktree`, `CMRU_PREFLIGHT_COMMIT` is the feature head, not origin/main.
13. When `git archive` fails (an unknown revision), the release exits 1 with the git diagnostic
    and runs no command.

Before merge, the main controller runs one real `cmru release cmru --dry-run` and one
`cmru release ciu --dry-run`, both with the engine built from the branch head.

## Migration order (no release runs without the checks)

- **M0 (P-core-12).** The `estate-lint` project (`estate-lint/suite`) holds the bootstrap literal check and is green.
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
  recipe), after the M1 dry-runs. A follow-up commit then deletes `RELEASE-TRANSACTIONS.md:84-92`.

## Out of scope

A `cmru preflight` verb; a `WARN` status; rows kept in transaction state for `--resume`; a
Run-Gate-owned identity preflight for every run-gate-gated project (run-gate backlog);
`gate_snapshot`'s other `cmru/...` evidence paths and moving it into `cmru/tools`;
`_is_docker_unavailable` in the built-in rows; removing the DinD row (KI-191); the
bootstrap-order test move and the estate lane itself (P-core-12); KI-141 part 7, `cmru/assay.toml`
edits, a template revision bump, scaffold examples.

## Open coordination items

1. **T3 (vbpub-assay)** confirms:
   - `campaign-<assay_lane>.{exit,json,txt,err}` is Assay's stable closeout naming;
   - `cmru-first-release-r2` writes no closeout files into the release gate;
   - KI-141 part 7 has landed: `_plan_gate` selects only `assay` and the first-release
     closeout set is gone, so the P-core-13 fixtures do not name the first-release lane;
   - that neither the smaller `release_preconditions.py` nor the deleted survivor tests
     (`test_r2_survivors_s1.py:326-341`, `:354-385`, `:388-480`) need a `cmru/assay.toml` change;
   - The question about reading `assay_lane` through `run-gate --list --json` moved to the run-gate
     stream (T2); see fix-forward item F1.
2. **The main controller:**
   - sequences P-core-13 after P-core-06 and after M0;
   - adds `estate-lint/suite` to the gate set of any merge that touches root `docs/`,
     because "gate every project dir a merge touches" does not reach root docs;
   - runs the two real dry-runs.
3. **The operator** confirms `python3 -I -B` over the plan's `python` (D9).

## Fix-forward items for the implementation (design review round 2, 2026-11-24)

The review found these after the decisions were accepted. The implementation applies them; none
changes a decision.
- F1 (D17). `run-gate --list --json` exists from run-gate 25.1.0 (`run-gate-list/1`, which
  carries `assay_lane`). Raise `RUN_GATE_MIN_VERSION` in `run_release_gate.py:36` from 24.0.0 to
  at least 25.1.0 in M1, or refuse naming that minimum.
  - The list command is not a pure read: for assay environments, `assay_inventory` starts a
    `tester-unified` probe that runs the editable assay install (`run_gate.py:9788`, `:10226`).
  - Call the identity-bound run-gate with `_lane_environment()`, state the cost, and refuse on a
    non-zero exit.
- F2 (D4, sweep). Judge an export's age by the `mkdtemp` directory's own mtime or a stamp file,
  never by the contents: `git archive` stamps entries with the commit time.
  - A SIGTERM also orphans the preflight child (own process group, no timeout left). Record it
    beside the export leak.
- F3 (Multi-family). The refusal raised at `cli.py:7589-7599` is "origin/main changed after the
  multi-family release preflight", and a single-family release reaches it too.
  - Use one message for both cases.
  - A dispatched child judges the handoff revision (`_consume_release_snapshot_handoff`), with no
    fresh fetch.
- F4 (D14/D15). Define `GatePreflightError` in `run_release_gate.py` itself, with
  `args == (detail,)` and `remedy` as an attribute.
  - The class must not be imported from cmru: an older engine's exec-from-blob would hit an
    ImportError.
  - `str(exc)` must stay the detail alone.
- F5 (D10). Remove every `_RESERVED_CMRU_INTERNAL_ENV` name (for example
  `CMRU_INTERNAL_RELEASE_PREFLIGHT_FD`, `config.py:1062`) except `CMRU_INTERNAL_PYTHON`, and
  add `CMRU_PREFLIGHT_MODE`/`CMRU_PREFLIGHT_COMMIT` to that reserved set.
- F6 (D16). The bootstrap check moves from release time to merge time (the `estate-lint`
  project, `estate-lint/suite`).
  - Name the owner of `test_bootstrap_from_zero_sequence.py` after P-core-12.
  - cmru's gate must not import `estate_test_policy` unless `tester-unified` ships it.
- F7 (Tests). In `tests/test_r2_survivors_s1.py`, everything from `_fake_git` (:315) to the end
  of the file (475 lines) goes. List the deletions by test name in the implementation, not by line.
- F8 (Docs). `docs/DESIGN-GUIDE.md:1066-1067` hard-codes `campaign-cmru` and a
  first-release closeout basename; update it with D17. KI-141 part 7 has landed, so only
  `campaign-cmru{...}` remains.
