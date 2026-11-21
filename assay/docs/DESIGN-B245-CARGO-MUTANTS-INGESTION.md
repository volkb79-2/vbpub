# B245 (R-3b) code-level design: D2 directory-artifact reservation, D7 argv placeholder

Read against `main` e765d6977. Every `file:line` is in `assay/` at that commit, given relative to `assay/`.

**Status (2026-11-18).** Binding design for backlog B245. It refines and, where it says so, CORRECTS `RUST-R3A-SPIKES.md` D1-D8 against the real code: D2 holds `<out>` (not `mutants.out`) and extends the B046 reservation (§1.1); D4's "total 0" is a choice of `total`, so a baseline failure becomes a report-decided R0 FAIL (R4); D1's `FnValue` binding needs the method/trait correction (R5); D7's variable becomes the whole-token placeholder `{assay.changed_diff}` and needs `git diff --relative` (R3, unmeasured). Author: an independent Opus design read (fresh agent, read-only); decisions Q1-Q4 are DECIDED in §6 (operator authority: D3 "as you see fit", v20 approved; controller agreed 2026-11-18). U0 (fixture re-capture on a host) is the first work package; every unit that changes `assay/src` needs the host `self-qualification-provisional` (B534) before merge.

## 0. Facts from the code that shape both decisions

- **Ingested R2 lanes always take the snapshot path.** `judge.mutation.format` selects ingestion (`src/assay/runner.py:4436`). Such a lane runs through `_run_prepared_lane`, and its baseline unit is the single `_execute_snapshot_unit` call at `runner.py:4629-4664`, which passes `result_report=lane.result_report` (4655) and `mutation_artifact` (4658). So D2's "snapshot-only" already holds for `judge.mutation`. Only `result_report` can reach the direct R0 path, and config must close that (§1.5).
- **A Stryker lane holds two independent reservations today.**
  - The B046 mutation report is reserved against `snapshot.project_root` (`runner.py:3708-3734`) and consumed after the post-run git check (3878-3898). An absent report is `NO_MEASUREMENT/EMPTY_COVERAGE` (3886-3896).
  - The B078 R0 report is reserved against `run_cwd` (1451-1456). `_reserve_result_report` (1154-1208) swallows every failure into `None`. It is consumed at 1628 (`_consume_result_report`, 1211-1240, which also swallows), and its `summary` decides R0 (1639-1696; `decided_by_report` at 1696).
  - The two paths use different grammars: `judge.mutation.artifact` is project-relative, `result_report.path` is relative to the run cwd (`config.py:1086-1094`).
- **`safeio.OutputReservation`** (`src/assay/safeio.py:246-405`):
  - It holds the PARENT fd of a regular-file basename.
  - A pre-existing directory is refused (`_require_safe_pre_existing`, 234-243), and so is a non-regular read target (`_safe_bounded_read`, 439-440).
  - A replaced parent shows as `st_nlink == 0` on the held fd (340-388, B049).
  - `_open_child_dir(create_missing=True)` does mkdir 0700 and then reopens with `O_DIRECTORY|O_NOFOLLOW` (138-180).
- **The post-run dirty check counts untracked files.** `git.dirty_paths` subtracts only a committed `.gitignore` (`src/assay/git.py:862-898`; checked at `runner.py:3801-3808`). `<out>/` must therefore be ignored by a COMMITTED `.gitignore`, or every honest run ends `DIRTY_TREE`. D2 does not mention this.
- **The ingested model is Stryker-shaped.**
  - `MutationFormatSpec.parse` is `Callable[[str], IngestedMutationReport]` (`src/assay/mutation_parsers/__init__.py:71-76`).
  - The report carries `project_root` and `sources` (`mutation_parsers/model.py:101-116`). `ingest_mutation_report` checks them in `_check_report_project_root` (`mutation.py:7877`) and `_check_report_source_matches_commit` (8022).
  - cargo-mutants embeds neither field.
- **The scope diff comes too late for D7.** The R2 scope diff is computed AFTER the baseline command, and only on R0 PASS (`runner.py:5040-5056`: `git diff --unified=0 base head` in `baseline_snapshot.root`). The base is resolved before any snapshot (`_resolve_declared_base`, 6647). D7 needs a diff BEFORE the command runs.
- **The argv invariant.** `argv_effective == argv_declared + argv_appended` is a verdict invariant: `runner.py:810-817` states it, `verify.py:233-256` checks it raw, and `verify.py:3175-3177` rebuilds it. The process boundary is `process_runner(plan.argv_effective, ...)` at `runner.py:1530`, which reaches `Popen(list(argv))` at 419.

## 1. D2: directory-artifact reservation

### 1.1 Decision: extend the B046 reservation, no parallel mechanism

The obstacle D2 names exists only if assay holds `<out>/mutants.out` as the parent fd. cargo-mutants rotates `mutants.out`, but it uses a pre-created `--output` directory as is (spike "Output reuse and rotation", step 5).

- The held fd therefore moves to `<out>`, which assay creates in the snapshot with the existing `create_missing_parents=True`.
- `mutants.out` becomes a child that must be ABSENT before the run and is walked with `O_NOFOLLOW` after it.
- The state machine stays RESERVED -> ARMED -> CONSUMED, with a different final object. It lives in `safeio` as a sibling class and is wired through the existing B046 `mutation_artifact` seam.
- **One consume serves both R0 and R2.** Two fds walking one tree twice could see different bytes, and the B078 side would swallow a refusal (`runner.py:1201-1207`).

### 1.2 New `safeio` API (exported in `__all__`, `safeio.py:33-38`)

```python
@record  # frozen
class DirectoryArtifact:
    artifact: str                 # "<out>/mutants.out", project-relative
    identity: tuple[int, int]     # (st_dev, st_ino) of mutants.out as opened
    report: bytes                 # mutants.out/<report_name>
    members: Mapping[str, bytes]  # "diff/<n>.diff" -> bytes, sorted keys
class DirectoryArtifactReservation:  # RESERVED -> ARMED -> CONSUMED/CLOSED
    def arm(self) -> None
    def consume(self, *, members: Callable[[bytes], Sequence[str]]) -> DirectoryArtifact | None
    def close(self) -> None            # idempotent, never unlinks
    consumed: DirectoryArtifact | None  # set by consume; the second reader uses it
def reserve_directory_output(project_root: Path, out_dir: str, *, child: str, rotated: str,
    report_name: str, report_limit: int, member_limit: int, max_members: int,
    create_missing_parents: bool = False) -> DirectoryArtifactReservation
```

cargo-mutants call values:
- `child="mutants.out"`, `rotated="mutants.out.old"`, `report_name="outcomes.json"`
- `report_limit=mutation_parsers.MAX_MUTATION_REPORT_BYTES` (`__init__.py:68`)
- `member_limit=1 MiB`
- `max_members=vocabulary.MAX_INGESTED_MUTANTS`

**Reserve.**
1. `_lexical_components(out_dir)` (56-82).
2. `_open_parent_chain` followed by `_open_child_dir(parts[-1], create_missing=...)`, so `<out>` itself is the held fd.
3. Record `out_identity = (st_dev, st_ino)` from `fstat(held)`.
4. `_lstat_relative` (214-218) `child` and `rotated`. If either is present, refuse with `ERROR/UNREADABLE_ARTIFACT`: in a fresh snapshot only the commit or a `link_path` can have put it there.
5. Never create `child`.
6. (Added by section 8 item 1, U0 measurement.) Refuse with `ERROR/UNREADABLE_ARTIFACT` when `<out>` itself is not EMPTY (hidden files count; checked by listing the held fd), because a non-empty in-tree `<out>` is copied into the tree under test. Test: `test_reserve_refuses_nonempty_out`.

**Arm.** Repeat both absence checks (B046's "appeared between reservation and arming" rule, 289-295) and, per step 6, the emptiness of `<out>`. Nothing is ever unlinked, because nothing pre-existing is ever accepted.

**Consume.** Every open uses `dir_fd`, `O_NOFOLLOW` and `O_CLOEXEC`.
1. **Held fd.** `fstat(held).st_nlink == 0` refuses with the B049 text: factor `_refuse_if_parent_was_replaced` (340-388) into a module function. Then re-walk `<out>` by name and compare `(dev, ino)` with `out_identity`. A rename-away keeps nlink above 0, so this comparison is what catches it.
2. **Rotation.** `rotated` present refuses: the tool rotated a `mutants.out` that was absent at arm, so the command ran it twice or something else wrote there.
3. **Child.** `child` absent returns `None`, the zero-mutant or no-run signal (§1.4). A symlink or non-directory refuses.
4. **Child fd.** Open `child` with `O_RDONLY|O_DIRECTORY|O_NOFOLLOW` from `held` and `fstat` it. A `(dev, ino)` that differs from the step-3 lstat is a swap, and refuses.
5. **Report.** `_safe_bounded_read(report_name, dir_fd=child_fd, limit=report_limit, require_single_link=True)` (408-461). A `None` here REFUSES ("mutants.out without outcomes.json: the tool died mid-run") rather than returning `None`.
6. **Members.** `names = members(report)` is the parser's `diff_paths(raw)`, each `diff_path` verbatim.
   - Each name must match `^diff/[A-Za-z0-9_.-]+\.diff\Z`.
   - The count must be at most `max_members`.
   - Open `diff/` with `O_DIRECTORY|O_NOFOLLOW` and read each member with `require_single_link=True`.
   - A missing member refuses.
7. **Finish.** Re-lstat `child` and compare it with step 4. Close every fd in `finally`, set `consumed`, and return.

Every refusal is a RAISED `ERROR/UNREADABLE_ARTIFACT`. On an ingested lane the artifact IS the evidence (`runner.py:3886-3893`).

### 1.3 Time window (D2 c): a parser completeness rule, not an fs check

- **Rule.** The runner passes `CommandResult.started`/`ended` (shape at `runner.py:1577-1578`) to the reader. The reader requires `started - 5 s <= start_time <= end_time <= ended + 5 s`.
- **Timestamps.** They are RFC 3339 with nanoseconds and a trailing `Z`, for example `2026-11-18T04:36:08.039112716Z` in `full/.../outcomes.json`. Truncate them to microseconds before comparing.
- **Tolerance.** `CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS = 5`. Comment that the tool shares the host clock source, so the check is not load-dependent.
- **Refusals.** `end_time: null` refuses as incomplete. A window miss refuses as "not this command's report".
- **Value.** It adds little beyond the fresh snapshot and the absent-before rule, but it is nearly free and catches a canned report copied into place.

### 1.4 Terminals

| Post-run state | R0 | R2 |
|---|---|---|
| `mutants.out` absent, exit 0 | exit code PASS | synthesised zero/zero `Mutation`, `INCONCLUSIVE/NO_MUTANTS` (D3, U5) |
| absent, exit 1/5/6/70 | exit-code FAIL | not reached (R2 needs R0 PASS, `runner.py:5040`) |
| `mutants.out.old` present (pre or post) | exit code | `ERROR/UNREADABLE_ARTIFACT` "rotated" |
| symlink, special file or hardlink anywhere in `<out>/mutants.out/{outcomes.json,diff/*}` | exit code | `ERROR/UNREADABLE_ARTIFACT` |
| `<out>` rm-rf'd and recreated, or renamed | exit code | `ERROR/UNREADABLE_ARTIFACT` (B049 text) |
| `mutants.out` without `outcomes.json` | exit code | `ERROR/UNREADABLE_ARTIFACT` "died mid-run" |
| `end_time` null or outside the window | exit code | `ERROR/UNREADABLE_ARTIFACT` |
| Baseline summary not `Success` (exit 4) | FAIL decided by report (U4) | not reached |
| `<out>` tracked at the commit | `BAD_LANE_CONFIG` pre-run (`_coverage_artifact_is_tracked`, as at 3716-3733) | none |

On a refusal, R0 keeps B078 semantics: an unusable report falls back to the exit code, and R2 carries the reason. The docstring should say that R0 means "the command ran" and R2 means "the evidence is unreadable".

### 1.5 Wiring

- **`mutation_parsers/__init__.py:71-90`.** `MutationFormatSpec` gains `artifact_kind: Literal["file", "directory"]`, `parse_directory | None`, `status_buckets`, `discarded_statuses` (U3) and `accepts_changed_diff: bool` (D7). Register `"cargo-mutants-outcomes"`.
- **`runner.py:3708-3734`.**
  - For the directory kind, use `safeio.reserve_directory_output`, with the tracked check on `<out>`.
  - Pass the ARMED reservation to `execute_plan` through a new keyword `directory_report` (1346-1357 to `_execute_plan_inner` 1511-1521). When it is set, `_reserve_result_report` is skipped.
  - Line 1628 becomes: consume once, then `summary = result_reports.read_verified("cargo-mutants-outcomes", consumed.report, context=window)`. `read_verified` (`result_reports/__init__.py:74`) gains a keyword-only `context`.
- **`runner.py:3878-3898`.**
  - For the directory kind, use `reservation.consumed` and never consume twice.
  - `None` together with an exit-code R0 PASS is the zero-mutant marker. Skip the `EMPTY_COVERAGE` error (3886-3896) for this kind.
  - Add `mutation_report_artifact: DirectoryArtifact | None` to `SnapshotUnitResult` (3490-3498).
- **`runner.py:5196-5217` and `_ingest_r2_report` (6097-6184).**
  - Dispatch on `artifact_kind`. The directory branch skips the UTF-8 decode (6135-6142) and calls `spec.parse_directory(artifact, ParseContext(run_cwd, repository=prepared, read_timeout, window))`.
  - It passes `content_tier="per-mutant-diff"` to `ingest_mutation_report` (`mutation.py:7685`), which then SKIPS `_check_report_project_root` and `_check_report_source_matches_commit`. The parser has already bound content. Filling `sources` from blobs and comparing them with the same blobs would be a tautology dressed as a check.
- **`config.py`.** `RESULT_REPORT_FORMATS` gains the new format (`_load_result_report`, 1100-1142). Cross-field `LaneConfigError`s go beside `ingested_r2` (2395-2402; `result_report` is loaded at 1991):
  1. `result_report.format == cargo-mutants-outcomes` iff `judge.mutation.format == cargo-mutants-outcomes`. This refuses R0-only lanes and closes the direct path.
  2. `result_report` is REQUIRED on such a lane, because otherwise exit codes 2/3 decide R0 (design §D).
  3. `PurePosixPath(lane.cwd or ".") / result_report.path == judge.mutation.artifact`.
  4. The artifact names `<out>`. A last component of `mutants.out` refuses, with a message stating the rotation fact.
  5. The argv `--output`/`--output=` value must equal `result_report.path`. `CARGO_MUTANTS_OUTPUT` in `env` is refused as a second spelling.

### 1.6 Tests (D2)

`tests/core/test_safeio_directory_output.py` (new, beside `test_safeio.py` and `test_safeio_replaced_output_directory.py`):
- `test_reserve_creates_out_and_holds_it_not_child`
- `test_reserve_refuses_preexisting_child`
- `test_reserve_refuses_preexisting_rotated`
- `test_reserve_refuses_nonempty_out` (hidden file included) and `test_arm_refuses_out_that_became_nonempty`
- `test_arm_refuses_child_appearing_after_reserve`
- `test_consume_real_rotation_refused`: copies the real `docs/rust-r3a/evidence/respike/o-git/`, which has both `mutants.out` and `mutants.out.old`.
- `test_consume_absent_child_returns_none`
- `test_consume_{child,report,diff,diff_dir}_symlink_refused`
- `test_consume_report_hardlink_refused`
- `test_consume_out_rmrf_recreated_refused`
- `test_consume_out_renamed_and_replaced_refused`
- `test_consume_child_swapped_between_lstat_and_open`: an `os.open` monkeypatch renames the child in between.
- `test_consume_missing_report_in_present_child_refused`
- `test_member_name_grammar`: `../x.diff`, `diff/a/b.diff`, absolute and non-`.diff` names are refused.
- `test_member_missing_refused`
- `test_member_count_bound`
- `test_member_size_bound`
- `test_consume_twice_runtime_error`
- `test_close_idempotent_never_unlinks`

`tests/core/test_runner_ingested_r2.py` (extend). A fake `process_runner` copies a real fixture tree into `<out>/mutants.out`, so no cargo is needed.
- `test_cargo_mutants_full_report_ingested`: 16 killed, 7 survived, 1 discarded, 1 budget_exceeded. R0 PASS is decided by the report at exit 3.
- `test_cargo_mutants_exit0_no_output_is_no_mutants`
- `test_cargo_mutants_exit5_no_output_is_r0_fail`
- `test_cargo_mutants_stale_old_is_error_not_pass`
- `test_cargo_mutants_end_time_null_refused`
- `test_cargo_mutants_report_outside_window_refused`
- `test_cargo_mutants_out_untracked_not_ignored_is_dirty_tree`
- `test_cargo_mutants_out_tracked_bad_lane_config`
- `test_r0_and_r2_read_one_consume`: a spy asserts consume is called once.

`tests/core/test_config_result_report.py` and `test_config_ingested_mutation.py`: one test per refusal 1-5 in §1.5.

## 2. D7: the `--in-diff` argv placeholder

### 2.1 Spelling

- **Token.** The exact whole token `{assay.changed_diff}`, defined as `vocabulary.ARGV_PLACEHOLDER_CHANGED_DIFF`.
- **Whole-token equality only.** The lane writes `"--in-diff", "{assay.changed_diff}"`. `--in-diff={...}` is refused.
- **Typos and misplacement.** A token that contains `{assay.` but is not a known placeholder is refused. The text is also refused in `env` values and in `environment_command`.
- **Scope.** `{assay.` is a reserved namespace. There is no template engine.

### 2.2 Lane-load validation

In `_load_lane`, after `argv` (`config.py:1783`) and beside the `ingested_r2` checks (2395-2402):
- **Allowed only when all of these hold:**
  - `"R2" in rigor`
  - `judge.mutation.is_ingested`
  - the spec has `accepts_changed_diff` (only `cargo-mutants-outcomes`)
  - `judge.mode == "changed_lines"`
  - a base is declared (`judge.base` or `base_source = "request"`, 2552-2590)
- **Exactly once** in `argv`.
- **REQUIRED** on a `cargo-mutants-outcomes` `changed_lines` lane. Without it the lane silently mutates the whole crate.
- **Refused** on `whole_target`, with a message naming the mode.
- **At run time**, a CLI-appended token equal to the placeholder is refused: only `argv_declared` is substituted.

### 2.3 Diff production and lifetime

- **When.** In `_run_prepared_lane`, after `baseline_snapshot` exists and BEFORE the baseline unit call (`runner.py:4629`). `resolved_base` is already final (6647, carried in `SnapshotSpec` at 6665). Reuse `check_resolved_base_is_head` as at 5045-5049, so the handoff and assay's scope see the same `(base_rev, head_rev)`.
- **How.** `git.run(baseline_snapshot.root, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--src-prefix=a/", "--dst-prefix=b/", "--no-renames", f"--relative={rel}", base_rev, head_rev)`.
  - `git.run` already isolates global and system config (`git.py:133-184`). The explicit prefixes defeat a repo-local `diff.noprefix`/`mnemonicPrefix`.
  - Default context (3 lines) is the measured-accepted shape (`evidence/respike/real-git.diff`). `--unified=0` was never measured.
  - `rel` = `project_prefix/cwd`, omitted at the repo top. **`--relative` is REQUIRED and unmeasured.** cargo-mutants resolves diff paths against the source tree (the run cwd), and every spike crate sat at the repo root.
  - `--no-renames` avoids the unmeasured rename case.
- **Where.** `scratch_root/changed-diff/in-diff.patch`, inside the `default_scratch_root` TemporaryDirectory (390-394, entered at 6657) and outside the snapshot tree.
  - The directory is 0700. The file is created with `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC`, mode 0600, and fsynced.
  - It is deleted when the TemporaryDirectory exits, so there is no extra cleanup path.
  - The command runs as the same uid in the same container.
- **Empty diff.** It is still written. cargo-mutants prints `Diff file is empty`, exits 0 and writes no output, which is the D3 case.
- **Plan field.** A new `CommandPlan.changed_diff: ChangedDiffHandoff(path, sha256, size, base_rev, head_rev, relative_to) | None` (class at 810, built at 1052). Only the baseline plan carries it.

### 2.4 Substitution point and the verdict

- **Where substitution happens.** Only at the process boundary. `runner.py:1530` passes `materialize_argv(plan)`, which is `argv_effective` with the single placeholder index replaced by `str(plan.changed_diff.path)`.
- **Missing handoff.** A plan that holds the placeholder with `changed_diff is None` raises `ERROR/BAD_LANE_CONFIG`. The literal must never reach Popen.
- **The verdict keeps the PLACEHOLDER** in `argv_declared`/`argv_effective`, and so does the `command_started` progress event (3758-3763):
  - A-036/A-129 (`verify.py:233-256`) needs no special case.
  - The temp path is per-run noise. Recording it would make identical runs differ in argv and lane declaration, which breaks byte-identical reruns and argv-keyed identity.
  - The reproducible facts are `(base_rev, head_rev, relative_to, sha256)`.
- **v20 field.** `judgment.r2.changed_diff = {base_rev, head_rev, relative_to, sha256, bytes}`. It is required iff `argv_declared` holds the placeholder, otherwise forbidden.
  - `verify.py` checks the biconditional, the shapes, and `base_rev`/`head_rev` against the judgment's resolved base and commit.
  - Document-only verify cannot recompute `sha256`. The field description must say so.

### 2.5 Tests (D7)

`tests/core/test_config_changed_diff_placeholder.py`:
- `test_allowed_on_cargo_mutants_changed_lines`
- `test_refused_on_native_r2`
- `test_refused_on_stryker_format`
- `test_refused_on_r0_only`
- `test_refused_whole_target`
- `test_required_on_cargo_mutants_changed_lines`
- `test_refused_twice`
- `test_refused_embedded_form`
- `test_refused_unknown_assay_placeholder`
- `test_refused_in_env_and_environment_command`
- `test_refused_without_base`

`tests/core/test_runner_changed_diff.py` (a fake runner records the argv it receives):
- `test_substituted_path_exists_outside_snapshot_mode_0600`
- `test_diff_has_ab_prefixes_despite_repo_noprefix_config`
- `test_diff_relative_to_lane_cwd_in_subproject`: crate under `sub/`, paths are `a/src/...`.
- `test_verdict_argv_keeps_placeholder`
- `test_changed_diff_field_matches_written_bytes_sha256`
- `test_diff_deleted_after_lane`
- `test_cli_appended_placeholder_refused`
- `test_plan_with_placeholder_and_no_handoff_errors`: the literal never reaches Popen.

`tests/core/test_verify_ingested_r2.py`:
- `test_changed_diff_required_iff_placeholder` (both directions)
- `test_changed_diff_base_mismatch_refused`

U7: a real run with the crate in a subdirectory and the diff produced by assay.

## 3. Work packages (S < 300, M < 900, L more changed lines)

Every unit updates README/DESIGN-GUIDE/CONSUMERS where behaviour changes, plus CHANGES.md `Unreleased`.

**U0 — Fixture re-capture (S, host v1001, no `src` change).**
- **Why.** The retained evidence is pruned. Only `full/` keeps every `diff/*.diff`; `respike/w-false` (the real `Failure(4)`, i.e. crashed, case) has no `diff/`, and `indiff/` has only `outcomes.json`. The D1 diff tier would refuse them, so they cannot be fixtures as retained.
- **What.** Re-run the s3-s4, workspace and respike scripts in `tester-unified-rust:local`, pruning only `lock.json` and `debug.log`. Add two subjects: a crate in a subdirectory with an assay-shaped `git diff --relative`, and impl/trait methods (R5).
- **Layout.** `tests/fixtures/rust/cargo-mutants/<case>/{subject/, mutants.out/, PROVENANCE.md}`.
- **Cases.** full, indiff, basefail-test, basefail-build, crashed-f4, w-true, zero-mutants, rotation, subdir, methods.
- **Synthetic cases.** `synthetic-signalled` and `synthetic-other` are edited from `full` and labelled synthetic.

**U1 — `safeio.DirectoryArtifactReservation` (M).** `safeio.py` plus the §1.6 safeio tests. No dependency.

**U2 — v20 schema cut (M).**
- **Namespace.** `vocabulary.py:414` becomes `("stryker", "cargo-mutants")`. The regex at 419-420 keeps its UNESCAPED `"|".join(...)` (a hyphen outside a character class is literal), because `tests/core/test_verdict_schema_is_packaged.py:66` asserts the schema pattern equals `INGESTED_OPERATOR_RE.pattern` character for character; do NOT introduce `re.escape` (it would emit `cargo\-mutants`). See §7.
- **Schema pattern.** `schemas/verdict.schema.json:1385` becomes `^(?:stryker|cargo-mutants):[A-Za-z0-9]+$`. Update the 1354 description and the step-sync test (`tests/core/test_config_vocabularies.py` or `test_docs_examples_and_vocabulary.py`).
- **Stryker parser.** `mutation_report_json.py:96` indexes the namespace by name, not `[0]`.
- **Version.** `verdict.py:427` `VERDICT_SCHEMA_VERSION = 20`.
- **New fields** (model and schema): `judgment.r2.changed_diff` (§2.4) and `judgment.r2.report_absent` (Q1).
- **`verify.py`.** The version gate, refusal of v19 documents (no shim), the changed_diff rules, and a verify test with an ingested `crashed` document (R8).
- **Fixtures and release.** Regenerate `tests/fixtures/verdicts`. This is a major release.

**U3 — `mutation_parsers/cargo_mutants_outcomes.py` (L; split into U3a grammar+statuses and U3b binding).**
- **API.** `parse_directory(artifact, ctx)` and `diff_paths(raw)`.
- **Grammar.** Closed keys at every level. `process_status` is one of `"Success"`, `"Timeout"`, `"Other"`, `{"Failure": int}`, `{"Signalled": int}`. Anything else refuses.
- **D5 normaliser.** It produces `Killed`, `Crashed`, `Survived`, `Timeout` or `Unviable`. A Build-only phase list is legal for `Unviable`. A mutant whose summary is `Success` or `Failure` refuses.
- **D1 spans.** Convert character columns to UTF-8 bytes on the blob, read via `ctx.repository.read_regular_file` (`isolation.py:784`). Reuse `mutation_report_json`'s column helper only if it is character-based.
- **Genre binding.** Per D1, with the R5 method correction.
- **Diff tier.** Every `-` and context line equals the blob, and the `---` path equals the mutant `file`.
- **Report fields.** `sources` come from the blobs, `project_root=None`, and the producer is `(cargo-mutants, cargo_mutants_version, "outcomes.json")` (Q2).
- **Registration.** `status_buckets={Killed: killed, Crashed: crashed, Survived: survived, Timeout: budget_exceeded}` and `discarded_statuses={Unviable: DISCARD_REASONS[0]}`. Generalise `mutation.py:7622` (`INGESTED_STATUS_BUCKETS`) and 7646-7653 to read these tables from the spec, with Stryker's tables moved into its spec unchanged.
- **Tests** (`tests/parsers/mutation/test_cargo_mutants_outcomes_parser.py`):
  - every U0 case;
  - refusals: unknown key, `end_time` null, mutant `Success`, span past EOF, token mismatch, diff minus-line mismatch, diff path mismatch, non-UTF-8 blob;
  - unicode columns (`s2/s2-uni-list.json`);
  - `Signalled`, `Other` and `Failure(4)` map to `Crashed`; `Failure(100)` maps to `Killed`.

**U4 — `result_reports/cargo_mutants_outcomes.py` (S).**
- **Reader.** `read(raw, *, window)` returns `total = 1 + total_mutants` and `failed = 0 if Baseline == Success else 1`. `finished` means `end_time` is set and inside the window.
- **Effect.** A baseline failure becomes an R0 FAIL decided by the report (`decided_by_report`, `runner.py:1696`). See R4 for why this differs from D4.
- **Tests.** basefail-test, basefail-build, full.

**U5 — Runner and config wiring (L; depends on U1-U4).**
- **Scope.** Wire §1.5 and §2.2-2.4.
- **Zero-mutant synthesis.** When there is no artifact and R0 PASSED by exit code, build:
  - a `Mutation` with every bucket empty and `candidate_count=0`;
  - an `IngestedMutationResult` with `producer_tool` per Q1, `survived_uncovered=()`, `discarded=()`, `lines_without_candidates` per Q3, and `fail_under` from `judge.mutation.fail_under`.
  - Then `judge_mutation` (`mutation.py:8317`) yields `INCONCLUSIVE/NO_MUTANTS` and `verify.py:1584-1586` is satisfied.
- **Tests.** The runner and config tests in §1.6 and §2.5.

**U6 — Docs (S).**
- CONSUMERS gets the "Rust R2 lanes" template: D8 flags, `{assay.changed_diff}`, `--output`, the committed `.gitignore` of `<out>/`, and an explicit `--test-workspace`.
- DESIGN-RUST-ADAPTER §D/§G status. K15 stayed until U8 (closed by the real hostobs run of 2026-11-18).

**U7 — `tools/qualify_rust_r2_real.py` (M, host).** Real cargo-mutants in `tester-unified-rust` on the U0 subjects, asserting each case's verdict, subdir and zero mutants included.

**U8 — Hostobs T3 registration (S; needs plan item T3-Q).** `[lanes.r2]` plus the run-gate lane with `--base <previous release tag>`. It stays outside every merge and release gate.

**Order.** See §7 item 11 (U5 is split into U5a/U5b/U5c; U0's extra measurements in §7 item 4 gate U1's final signature). Base order: U0, U1 and U2 in parallel; then U3 (needs U0 and U2) and U4; then U5; U6; U7; U8.

## 4. Risks found in D1-D8

- **R1 (D2 framing).** "The held fd would end up on `.old`" is true only when assay holds `mutants.out`. Holding `<out>` (measured: it is used as is) removes the problem, so D2 extends B046 (§1.1). The real hazard would be two parallel reservations (B078 plus B046) over one tree, because B078 swallows refusals (`runner.py:1201-1207`).
- **R2 (D2 gap).** D2 ignores the post-run `DIRTY_TREE` check (`git.py:862`, `runner.py:3801-3808`). Without a committed `.gitignore` of `<out>/`, every run fails. This needs a CONSUMERS line and a test.
- **R3 (D7 gap, unmeasured).** Every spike crate was at the repo root. For hostobs inside vbpub, the diff needs `--relative=<project>/<cwd>`, or `--in-diff` either mismatches (exit 5) or filters nothing. U0 and U7 must measure this.
- **R4 (D4).** "verify_complete cannot express total 0" follows from a choice of `total`, not a fact. With `total = 1 + total_mutants`, a baseline failure is a report-decided R0 FAIL. That beats a refusal riding exit 4, whose reason `_consume_result_report` swallows (`runner.py:1233-1240`). `decided_by_report` supplies the distinguishability D4 asks for.
- **R5 (D1 FnValue binding).** "`fn <name>` inside the function.span text" is unmeasured for methods, trait impls and generics, where `function_name` is likely qualified (`Type::method`, `<impl X for Y>::m`). Bind on the last `::` segment with generics stripped, plus the diff tier, and add a methods subject to U0. Otherwise the first real hostobs run refuses every method mutant.
- **R6 (D1 diff tier).** The tier needs a `diff/*.diff` for every mutant, and the retained evidence pruned most of them (evidence README), so U0 is a prerequisite. `mutants.json` embeds the same diff, so a cheap cross-check is possible (Q4).
- **R7 (D3).** "Fill `producer_tool` from the lane declaration" has no source for `version`: the lane declares none, and the schema requires a `mutation_producer_tool` object (`verdict.schema.json:2399`). See Q1.
- **R8 (D5).** `crashed` is in `MUTATION_BUCKETS` (`verdict.py:1038-1045`), but no ingested verdict has carried it so far. Audit `_check_producer_fork` and `_check_discarded_disposition` (`verdict.py:3697ff`) for an implicit "ingested has no crashed" assumption. U2 adds the test.
- **R9 (D8).** `--jobs N` with `--test-workspace=true` multiplies the per-mutant cost by the whole-workspace test time. The §D estimate of 5-20 min assumed per-crate tests. Measure before U8.
- **R10 (D2 c).** Keep the window in UTC with a named, test-pinned tolerance. It must never become load-dependent.

## 5. Host versus devcontainer

- **Host `self-qualification-provisional`** (any `assay/src` change): U1, U2, U3, U4, U5. U7 and U8 run on hosts by nature.
- **Earlier, in the devcontainer:** the U0 layout and PROVENANCE drafting (the capture itself needs v1001 and the image), U6 drafts, and the U1/U3 test files written against this spec.
- **Before U3:** a throwaway v1001 probe settles the only two unmeasured parser inputs, `--relative` (R3) and method binding (R5).
- **Verdicts** come from the registered gate only, never from local pytest.

## 6. Decisions on the open questions (DECIDED 2026-11-18, the recommendation of each)

- **Q1 (zero-mutant `producer_tool`). DECIDED:** name `cargo-mutants`, version the literal `"unobserved"`, `report_schema_version` `"outcomes.json"`. The schema permits that literal only with `judgment.r2.report_absent: true`, `mutation.total == 0` and `NO_MUTANTS`. The alternative, a lane-declared version, is a key that drifts from the image.
- **Q2 (`report_schema_version`). DECIDED:** cargo-mutants has none; use the assay-owned contract string `"outcomes.json"`, and refuse any `cargo_mutants_version` outside the major qualified by U7 (`27.`). That turns the design's "a format change fails qualification" into an actual mechanism.
- **Q3 (zero-mutant `lines_without_candidates`). DECIDED:** the diff's added lines within source roots, not an empty list. That keeps "nothing to mutate" reviewable, which is the field's stated purpose (design §D).
- **Q4 (`mutants.json` embedded-diff cross-check, R6). DECIDED:** yes, part of B245, in U3b. It is cheap.

## 7. Corrections from the independent review of this document (2026-11-18, BINDING)

A fresh reviewer checked about 50 citations (all sound; only drift: `scratch_root_factory()` is entered at `runner.py:6658`) and found the problems below. Where this section contradicts an earlier one, THIS section wins.

1. **Schema pattern equality (U2).** Keep the unescaped `join`; the schema literal is `^(?:stryker|cargo-mutants):[A-Za-z0-9]+$` and `test_verdict_schema_is_packaged.py:66` pins it equal to `INGESTED_OPERATOR_RE.pattern`.
2. **Consume result is a four-state value, not `DirectoryArtifact | None` (U1, U5).** `_consume_result_report` swallows every `AssayError` (`runner.py:1233-1240`) and `execute_plan` returns before `1628` on timeout and process-tree errors, so "`consumed is None`" would conflate absent, refused and never-consumed, and a refusal on an exit-0 run would read as the zero-mutant `NO_MUTANTS`. The reservation returns `NotConsumed | Absent | Refused(error) | Present(artifact)`. Only `Absent` after a COMPLETED run with R0 PASS by exit code is the zero-mutant marker. `Refused` is stored on the unit result and re-raised at `runner.py:3878` as `ERROR/UNREADABLE_ARTIFACT`. `NotConsumed` (timeout, process error) is never `NO_MUTANTS`. The `assert unit.mutation_report_bytes is not None` near `runner.py:5205` must be reworked for the directory kind (U5). Tests: one per state, including "refusal on an exit-0 run is ERROR, not NO_MUTANTS".
3. **Memory and parse bounds (U1, U3, U4).**
   - Aggregate cap: `MAX_DIRECTORY_MEMBERS_TOTAL_BYTES = 64 MiB` over all `diff/*.diff` (reading stops with a refusal when exceeded), `member_limit` 256 KiB, `max_members` `MAX_INGESTED_MUTANTS` (100_000 is the count bound, the byte bound is the aggregate).
   - Parse `outcomes.json` ONCE (`json.loads` into one object, with the existing `RecursionError` discipline, B075) and hand the parsed object to `diff_paths`, the R0 reader and `parse_directory`; the R0 reader's `MAX_RESULT_REPORT_BYTES` (8 MiB) is deliberately NOT applied to this kind, the directory report limit (`MAX_MUTATION_REPORT_BYTES`) is, and a comment says why.
   - Member-name grammar: the measured names (`src__lib.rs_line_9_col_5_001.diff`) pass; a source path containing a space or non-ASCII characters is REFUSED (fail-closed) until U0 measures how cargo-mutants spells such names; the baseline outcome has `diff_path: null` and `diff_paths` skips it.
4. **U0 must also measure (prerequisite of U1's final shape).** Every spike used an ABSOLUTE `--output` outside the source tree. The design needs a RELATIVE `--output <dir>` inside the snapshot tree, which cargo-mutants copies to `/tmp`. Measure: (a) cwd-relative resolution, (b) whether an in-tree `<out>` is excluded from or included in the copied tree and whether it is mutated or built, (c) in-tree `<out>` accepted at all, (d) a NON-empty pre-created `<out>`, (e) `--output` of a crate in a subdirectory and a workspace root ABOVE the run cwd (R3), (f) source paths with a space and non-ASCII characters, (g) methods and trait impls (R5), (h) `git diff --relative` anchoring (R3). If (b) shows the in-tree `<out>` is copied into every mutant tree, D2 changes (an `<out>` outside the tree is not reservable by a project-relative path): stop and escalate.
5. **`changed_diff` biconditional (U2, §2.4).** `judgment.r2.changed_diff` is required iff `judgment.r2` exists AND `argv_declared` holds the placeholder AND the lane is ingested cargo-mutants changed_lines; forbidden otherwise. Refusal verdicts that end before the R2 claim (`BAD_LANE_CONFIG`, `GIT_FAILED` while producing the diff, snapshot failure, budget exits) carry the placeholder in `argv_declared` but no `judgment.r2`, and must verify unchanged. Test both directions, including a `refuse_lane` document.
6. **D7 plumbing (U5).**
   - `_run_prepared_lane` (`runner.py:4363-4404`) has no `scratch_root` parameter; add one and thread it from the `run_lane` call at `runner.py:6658` (only `liveness_dir` and `witness_dir` are threaded today).
   - `git.run` decodes UTF-8 and raises `GIT_FAILED` on non-UTF-8 output (`git.py:669-689`). The changed-diff production uses bytes: make `_run_bytes` a public `git.run_bytes` (a B105 inventory touch) and restrict the pathspec to the lane's source roots (`-- <source_roots>`), so an unrelated non-UTF-8 change cannot ERROR the lane.
   - `_window_host.py:114-126` serialises `CommandPlan` against a strict key set; `changed_diff` needs a default and stays out of that key set (the window host is native-R2 only, `mutation.py:6260`).
7. **U2 gate prerequisites that were missing.**
   - `tests/core/test_docs_examples_and_vocabulary.py:~118-136` requires `## Migration notes (v19 to v20)` in `docs/CONSUMERS.md` plus the `CHANGES.md` schema note: they belong to U2, not U6. The same test loads every TOML example in README and CONSUMERS as a lane, so U6's template must pass the new config checks.
   - `tests/fixtures/b105-coverage-exclusions.json` is keyed by LINE numbers (`config.py` 100-101, `mutation.py` 204/211/212, `git.py` 405/406/1407/1408 after B552, `liveness.py`, `mutation_witness.py`) and is compared exactly (`conftest._validate_b105_exclusion_inventory`). Every unit that adds a line above one of those pragmas must update the map; re-check it in EVERY unit, not once. (B552 already moved the `git.py` pair.)
   - The whole-source 100 % R0/R1 coverage floor applies to every new line, including the new safeio class and its cleanup branches.
8. **Window check in the runner tests (U3, U5).** The fixtures carry real 2026-11-18 timestamps; a fake `process_runner` therefore fails the §1.3 window unless the test retimes the fixture (a helper `retime(outcomes, started, ended)` that rewrites `start_time`/`end_time` into the fake clock's window) or injects the clock. Use the helper; never loosen the tolerance for tests.
9. **Model and registry changes (U2, U3).** `IngestedMutationReport.project_root` (`mutation_parsers/model.py`) is a required `str` today and becomes `str | None`; `MutationFormatSpec` is a NamedTuple with only `parse` and `sniff` (`__init__.py:71-76`) and gains the fields of §1.5; `load_mutation_report(text, declared_format)` has no bytes/directory entry point, so add `load_mutation_directory`; `ingest_mutation_report` (`mutation.py:7685-7696`) has no `content_tier` parameter, it is new and needs a spec.
10. **Terminals to state (U5 tests).** `judge_mutation` precedence (`mutation.py:8300-8350`): `total == 0` -> `INCONCLUSIVE/NO_MUTANTS` (or `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` if `candidate_count - discarded > 0`); then any `crashed` -> `ERROR/EXEC_FAILED`; then any `budget_exceeded` -> `BUDGET_EXCEEDED/LANE_TIMEOUT`; then `hung`; then survivors below `fail_under` -> `FAIL/MUTANTS_SURVIVED`. Consequences: (a) with cargo-mutants' default per-package testing, a crate whose tests live elsewhere produces `crashed` mutants and the lane ends `ERROR/EXEC_FAILED` (loud, intended; the lane must declare `--test-workspace` explicitly); (b) the `full` fixture (16 killed, 7 survived, 1 timeout, 1 unviable) judges to `BUDGET_EXCEEDED/LANE_TIMEOUT`, not `FAIL`, because the timeout bucket outranks survivors; `test_cargo_mutants_full_report_ingested` asserts exactly that, and a separate fixture without the timeout mutant asserts `FAIL/MUTANTS_SURVIVED`.
11. **Splits and order.** U5 (L) is split: U5a config validation + directory reservation wiring and R0/R2 single consume; U5b the D7 diff production, placeholder substitution and `changed_diff` verdict field; U5c the zero-mutant synthesis. Order: U0, U1, U2 in parallel; U3, U4; U5a; U5b; U5c; U6; U7; U8; U0's section-4 measurements gate U1's final signature.
12. **Minor, noted.** An in-tree `<out>` and the post-run descendant window: the ingested lane uses `default_process_runner` (`runner.py:4445`), so a leftover cargo child could still write after exit; the final re-lstat protects directory identity, not file contents (low impact, a named limit).
13. **Who raises (U1/U5 API, clarifies 2 and §1.2/§1.6).** `safeio` `consume` RAISES on every refusal (the §1.6 `test_consume_*_refused` names stand). Because `execute_plan` swallows exceptions at `runner.py:1628`, a thin runner-side wrapper catches the raise there and records the four-state value (`Refused(error)`) on the reservation/unit result; the second reader at `runner.py:3878` re-raises it as `ERROR/UNREADABLE_ARTIFACT`. `Absent` is returned (not raised) by `consume`; `NotConsumed` is the wrapper's value when `execute_plan` returned before consuming.
14. **Parsed object versus bytes (U3/U4, clarifies 3).** `result_reports._READERS` and `read_verified(format, raw)` take bytes and §1.2's `members` callable takes bytes. Parse once with a small cache keyed on the report bytes' sha256 inside the directory-kind code path (one `json.loads`, shared by `diff_paths`, the R0 reader and `parse_directory`), not by changing the generic reader signatures.
15. **Order (clarifies 11).** U1 may START in parallel with U0 but cannot FINISH until U0 reports the §7.4 measurements; U2 and U3's grammar work do not depend on U0 except for the fixtures.
16. **Superseded numbers.** `member_limit` in §1.2 is 256 KiB (§7.3), not 1 MiB; `consumed.report` in §1.5 means `Present.artifact.report`; the `-- <source_roots>` pathspec of §7.6 must carry the project prefix (git runs anchored at the repo top with literal pathspecs), which U0 measurement (h) confirms.
17. **Line numbers and sequencing.** The `git.py` pragma pair is 1407/1408 only AFTER backlog B552 (branch `assay-b552-20261118`) is merged; on main before that it is 1384/1385. Merge B552 before U1, or every unit re-derives the pair from the file. The `scratch_root` of §7.6 is threaded from the `scratch_root_factory()` entry at `runner.py:6658`, which sits inside `_run_higher_rigor_lane` (def at 6441), not `run_lane` (6910).
18. **U0 is M, not S, and has a stop gate.** Its brief MUST include §7.4 (a)-(h) and §7.8's retiming helper; §7.4(b) (in-tree `<out>` copied into every mutant tree) is a stop-and-escalate gate, not a pass/fail case.

## 8. U0 results and their binding consequences (2026-11-18, `RUST-B245-U0-MEASUREMENTS.md`)

U0 is DONE: fixtures are in `tests/fixtures/rust/cargo-mutants/` (case list and measured result of each in its `PROVENANCE.md`), the measurements of section 7 item 4 are in the report named above. Where this section contradicts an earlier one, THIS section wins.

1. **Stop gate (7.4 b) fired, the design stands with a measured precondition.** With controls (`RUST-B245-U0-MEASUREMENTS.md`): a NON-empty in-tree `<out>` (marker file) is copied into the tree under test, even when a committed `.gitignore` ignores it; an EMPTY pre-created `<out>` is safe and the `mutants.out` the tool creates inside it is not visible in the tree under test. So the precondition is: assay creates `<out>` EMPTY in a fresh snapshot, never writes into it, and REFUSES a non-empty `<out>` at reserve and at arm time (added to the §1 Reserve and Arm steps as step 6; U1 pins it in `test_reserve_refuses_nonempty_out`); U5a additionally refuses a tracked `<out>` and requires a committed `.gitignore` entry for it (the post-run dirty check, not the copy). Not measured: whether the empty `<out>` directory itself appears in the copy.
2. **Diff anchor is the cargo workspace root, not the cwd (7.4 e, h; replaces 2.3's `rel`).** The diff is `git diff --relative=<workspace root relative to the git top>` (a lone crate is its own workspace root). Measured: with a workspace-root-relative diff the match is the same from the workspace root and from a member directory (5 mutants both ways), and a member-relative diff matches nothing with exit 0. The tool therefore does not require the lane cwd to equal the workspace root; U5a derives the workspace root (it is not a new lane field) by walking up from the lane cwd to the first `Cargo.toml` that contains `[workspace]` (or the cwd's own package when none), refusing `BAD_LANE_CONFIG` when none is found inside the repository. A wrong anchor filters everything silently, but that outcome is the legitimate zero-mutant shape and ends `INCONCLUSIVE/NO_MUTANTS` (D3), never PASS; there is NO runtime "diff touches a source path else BAD_LANE_CONFIG" check (an empty diff is the normal docs-only case and §7.6 already restricts the diff to the source roots). U7 qualifies the anchor on the real hostobs layout (inferred, not yet measured: a workspace root below the git top with several members). U5a passes the workspace root, not the lane cwd, as `ParseContext.run_cwd`.
3. **Member-name grammar (replaces 7.3's ASCII rule).** `diff_path` is taken verbatim and accepted when it is exactly `diff/<name>` with `<name>` one non-empty path component that ends in `.diff`, contains no NUL, `/` or `\`, and is not `.` or `..`; it is opened by name through the held directory fd (never by globbing), so any other character is safe. Non-ASCII names (`src__módulo.rs_line_2_col_5.diff`) pass; open them with the BYTES form of the name (`os.fsencode`) and test under a `C` locale. A duplicate `diff_path` across two outcomes refuses the report (whether two source paths can collide after the tool's space-to-underscore replacement was not measured, so the duplicate check is the guard, not an impossibility claim).
4. **FnValue binding (replaces 7.R5/D1's `fn <name>`).** `function_name` is qualified (`S::bump`, `<impl T for S>::triple`, `pick`). Bind on the LAST path segment after the final `::` (for `<impl T for S>::triple`, `triple`), requiring `fn <segment>` inside the blob text of `function.span`; the per-mutant diff tier still decides content (the last-segment binding cannot tell `S::bump` from `T::bump`; the diff tier does). Tests use the `methods` fixture; raw identifiers (`r#fn`) are untested.
5. **Workspace member directory named `b` (and, to be safe, `a`).** A member DIRECTORY named `b` matched 0 mutants whatever the package name (`RUST-B245-U0-MEASUREMENTS.md`); `crate_b` and `b2` matched. The U7 lint refuses member directories named `a` or `b` at the workspace root; the cause is unexplained (a guess is the tool's handling of the `b/` diff prefix), so do not rely on any theory.
6. **Fixtures.** `crashed-f4`, `basefail-build` (Build `Failure(102)`) and `zero-mutants` (no `mutants.out` and no `--output` directory) exist as real cases; each real case carries the tool's real `exit-code.txt`; the two `synthetic-*` cases are the ONLY edited ones and every test using them says so. `log/` is not kept, so `log_path` values dangle by design.
7. **Exit codes (measured, per case).** `full` 3, `indiff`/`paths`/`rotation`/`subdir` 2, `basefail-test`/`basefail-build` 4, `zero-mutants`/`crashed-f4`/`w-true`/`unicode`/`methods` 0. A wrong `--in-diff` anchor also exits 0.
8. **U5 wiring of the R0 reader (U4 review).** U5 clears the `load_document` memo after the single consume, and passes `ReportWindow(started, ended)` to the reader via `read_verified`'s context keyword; config refuses result_report format `cargo-mutants-outcomes` until then.
9. **A usable fresh report decides R0 (U5a review).** Like `vitest-json`, a usable, fresh (inside the command window) `outcomes.json` decides R0 whatever the command's exit code is (1, 70, -9 or 4): the report, not the exit code, is the evidence of the baseline test run. Exit 4 with a report of a failed baseline is an R0 FAIL decided by that report. Only an absent, stale or unusable report leaves the exit code to decide R0 (and R2 then fails closed as `ERROR`/`UNREADABLE_ARTIFACT`).
10. **U5b known limits (U5b review).** The explicit diff prefixes and `--no-ext-diff`/`--no-textconv` are defence in depth that no test can pin; a per-run temp path printed by the tool can appear in stdout/stderr tails of failing runs (breaks byte-identical reruns only there). The handoff file is re-read after the baseline unit and a changed, missing or unreadable file makes R2 `ERROR`/`UNREADABLE_ARTIFACT`; a lane with the placeholder may not declare R3.
11. **U5c: zero-mutant synthesis.** An absent report (the tool wrote no `mutants.out`) with exit 0 on a `changed_lines` lane that was handed the placeholder diff ends `INCONCLUSIVE`/`NO_MUTANTS` with `judgment.r2.report_absent`: R0 PASSED by exit code, and R2 is a `Mutation` with every bucket empty and `candidate_count` 0, the `producer_tool` is `cargo-mutants` / `unobserved` / `outcomes.json` (Q1), `lines_without_candidates` is the diff's added lines under the declared source roots (Q3), and `fail_under` is the lane's. Every other absent state stays fail-closed: a lane without the handoff (`whole_target`) is `ERROR`/`UNREADABLE_ARTIFACT`, a failed exit decides R0 by the exit code and R2 is not reached, a rewritten handoff file is refused before any synthesis, and a refused or never-consumed directory is never the zero-mutant marker. Known limits of the signal. (a) `lines_without_candidates` is parsed from the diff with rename detection ON (`diff.parse_added_lines`, `git diff --unified=0`) whereas the handoff diff given to the tool is built with `--no-renames`: a pure rename into a source root shows as an empty list although the tool was shown the file. This is a documented blind spot (R1 shares it). (b) The absent-report signal proves nothing about the tool having run: `cargo mutants ...; true`, a CLI-appended `--list`, or an appended `--output elsewhere` under `allow_argv_append` (which config does not look through) give exit 0 with no report and now yield `INCONCLUSIVE`/`NO_MUTANTS` (the producer is labelled `unobserved`; never a PASS, and run-gate maps `INCONCLUSIVE` to FAIL) instead of `ERROR`/`UNREADABLE_ARTIFACT`; the `--output elsewhere` case may hide mutants that really ran. (c) A wrong anchor (design item 2) is visible only through a non-empty `lines_without_candidates` together with a non-zero `changed_diff.bytes`; a comment-only change gives the same signal, so the verdict cannot separate the two. No WARN is emitted (a possible follow-up).
