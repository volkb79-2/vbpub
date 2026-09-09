# assay wave B074 + B077 — implementer REPORT (acceptance boxes)

Branch: `feat/assay-b074-b077-quickwins-2026-09-08`
Gate-verified commit: **`427157c1b5b04807a55c1544b05014a6add1b1a7`**
Registered gate `tester-unified`: **PASS (exit 0)**, verified from
`scratchpad/b074-b077-gate2.log` in a separate read, never a piped exit code.
Narrative and rulings: `assay-WAVE-B074-B077-LOG.md` beside this file.

---

## B074 — `judge.allow_test_path_targets`

| # | acceptance line (verbatim from the entry) | status | evidence |
|---|---|---|---|
| 1 | a `whole_target` lane naming `tests/<...>/lib.py` WITH the flag set is JUDGED, reaching a real `PASS`/`FAIL` on its coverage floor rather than `BAD_LANE_CONFIG` | **MET** | `test_the_flag_reaches_a_real_pass_on_the_coverage_floor` (PASS, 2/2, `considered == 1`) and `test_the_flag_reaches_a_real_fail_on_the_coverage_floor` (FAIL, 1/2, the uncovered line named against the target). Both against the REAL `PythonAdapter`. |
| 2 | the SAME lane WITHOUT the flag still refuses `BAD_LANE_CONFIG`, naming the target and the test-path gate | **MET** | `test_the_same_target_without_the_flag_still_refuses` — identical call, flag omitted; asserts `Outcome.ERROR`, `ReasonCode.BAD_LANE_CONFIG`, the target path in the message, `"test path"` in the message, and (beyond the box) that the message names the remedy. `test_evaluate_targets_without_the_flag_refuses_the_same_target` repeats it one level up. |
| 3 | a `changed_lines` lane over a diff touching that same file still SKIPS it | **MET** | `test_the_changed_line_sweep_still_skips_the_same_file`: `considered == 0`, `executable == 0`, no `files_missing_coverage`, and a `read_source_text` that raises if the skipped path is ever read. Strongest form: the test also asserts `evaluate_coverage` has no such parameter at all, so there is no argument a caller could pass to change it. |
| 4 | a target that is a genuine test file still refuses even WITH the flag | **MET — and the open fork is ruled and stated** | `test_a_genuine_test_file_still_refuses_with_the_flag[test_lib.py]` and `[conftest.py]`; `test_a_test_filename_outside_a_test_directory_also_refuses_with_the_flag` (`src/conftest.py`). The directory/filename split is proven per-adapter in `test_is_test_filename_splits_directory_from_filename_per_adapter` (Python, JavaScript, SQL) and `test_go_has_no_directory_branch_so_the_flag_relaxes_nothing_for_it`. Ruling and reasoning: LOG § "the two open forks, decided" (a). |
| 5 | the flag appears in the verdict's resolved judgment | **MET, with a documented call** | `judgment.r1.allow_test_path_targets`, emitted only when true. `test_allow_test_path_targets_is_recorded_when_the_lane_opted_in`; `test_the_opted_in_flag_survives_verify_and_reconstruction` (a real fixture document through `verify_document`, zero problems). Additive, **no `VERDICT_SCHEMA_VERSION` bump** — reasoning and the in-version precedent (`5b2730b6`) in LOG § (b). |

### Binding constraints from the wave prompt

| constraint | status |
|---|---|
| Shape 1 (flag), not Shape 2 (drop the veto) | **held** — the veto stands by default and for every non-declaring path. |
| `evaluate.py:428`'s sweep-side check untouched | **held** — unmodified; acceptance 3 proves the behaviour. |
| `mutation.py:470/478`'s check untouched | **held** — unmodified. |
| the other four `_resolve_whole_target` gates unchanged and still applying with or without the flag | **held, and tested WITH the flag set** — five tests: symlink, source-root containment, regular-file/directory, excluded-directory, adapter-recognised-source. |
| `assay verify` unaffected by B074 | **held for every pre-B074 artifact** — `test_a_verdict_without_the_flag_still_verifies_unchanged`. `verify` was *extended* to accept the new optional key (required: the schema and the reconstructor are two independent gates and `_reconstruct_judgment_r1`'s own rule is that a new field is registered in the same commit) and to refuse it under `changed_lines` mode or spelled as an explicit `false`. |
| `docs/CONSUMERS.md` gets the flag with a worked, paste-able example | **held** — new subsection with a full lane file, validated by `test_docs_examples_and_vocabulary.py`, which loads every live example through the real `load_lane_file`. |
| no verdict-schema *version* change | **held** — `VERDICT_SCHEMA_VERSION == 11`, `$id` unchanged, `schema_version` const unchanged. See the LOG for how this was squared with acceptance line 5. |

---

## B077 — a symlinked destination gets a named refusal

| # | acceptance line (verbatim from the entry) | status | evidence |
|---|---|---|---|
| 1 | a `--state-dir`/`--progress` destination reached through a symlink whose target is INSIDE the judged tree (and correctly gitignored) refuses with a message naming the symlink and the traversal, not a raw `fatal: pathspec ... is beyond a symbolic link` passthrough | **MET** | `test_a_state_dir_reached_through_a_symlink_names_the_link_not_gits_stderr` and `test_a_progress_destination_reached_through_a_symlink_refuses_the_same_way`. Each asserts `GIT_FAILED` is gone, the raw passthrough form (`fatal: pathspec '<path>`) is gone, and the message names the link, the traversal, `BAD_LANE_CONFIG`, and the real destination to use instead. The tree is left clean. |
| 2 | the two ALREADY-correct outcomes stay correct: a destination genuinely outside the repository, and one reached with no symlink involved | **MET** | `test_a_destination_genuinely_outside_the_repository_is_unaffected` (outside, *and* reached through a symlink outside the tree — runs to completion, records written, tree clean); `test_the_real_destination_behind_the_link_is_still_accepted` (the remedy the refusal names actually works). The two shipped SF-1 probes (`..._does_not_fail_open_across_an_intermediate_symlink`, `..._holds_when_the_root_itself_is_reached_by_symlink`) and the N2 pathspec-magic test all still pass unchanged. |
| 3 | a regression test reproduces the reviewer's exact repro (a symlink inside the tree pointing at a gitignored location, both `--state-dir` and `--progress`) and confirms the new message | **MET** | `_seed_with_a_committed_symlink_to_a_gitignored_store` builds precisely that — a **committed** symlink (so the tree is clean and the case is not confused with `DIRTY_TREE`) pointing at a **gitignored** directory — and both flags are driven through the real `main()`. The controlled wrong implementation (guard removed) was run and reproduced `ERROR/GIT_FAILED` with git's raw stderr for both flags; see LOG. |

### Calls made inside the fix shape

| question | call | why |
|---|---|---|
| reason code | `ERROR`/`BAD_LANE_CONFIG`, not `GIT_FAILED` | the entry's own "or a more specific reason code, if one already exists" clause — answered by the two sibling refusals in the same function. Git did not fail; the destination cannot be asked about. |
| which path components to probe | directory components only (`probe.parts[:-1]`) | measured: `check-ignore` answers normally (rc=1) about a final-position symlink. Probing all components would refuse a question git can answer. `test_the_guard_probes_directory_components_only`. |
| a symlink in the final position | left to its own older, earlier refusals | `--state-dir` requires a directory, `--progress` an ordinary regular file; both fire before the git question. `test_a_destination_that_IS_a_symlink_is_still_refused_by_its_own_older_guard` pins both. |

---

## Flags for the controller

1. **`CHANGES.md` will conflict at merge.** assay **6.0.0** was released to main
   (`ec0bc47f`) while this wave was in flight, so main's `[Unreleased]` block
   has been folded into the 6.0.0 section. This branch's two entries (one
   Added for B074, one Fixed for B077) are additive and belong in the *next*
   Unreleased block.
2. **A pre-existing backlog-ID collision, not created here.** Eight shipped
   comments/docstrings across `verify.py`, both coverage parsers,
   `adapters/go_stmtpos.py` and three test files say **"B074"** meaning the
   untrusted-JSON `RecursionError` sweep, which the backlog **renumbered to
   B075** at merge time after those comments shipped. Deliberately not touched
   (out of this wave's stated scope); a disambiguating note is left at the head
   of `tests/test_evaluate_whole_target_allow_test_path.py`. Worth a one-line
   follow-up commit or a backlog note.
3. **Backlog checkboxes not ticked.** B077's `- [ ]` acceptance boxes in
   `4-backlog.md` are left unchanged — `4-backlog.md` is shared with the
   concurrent B078 wave and every other filer, so editing it here would create
   a merge conflict for no benefit. Tick at merge.
4. **One transient local-suite failure was environmental, not this work** — the
   concurrent `cmru release` swapped the shared venv's installed `assay` dist
   (5.2.0 → 6.0.0) mid-run. Confirmed clean both with and without these changes
   when run in isolation; the registered gate builds its own venv and is
   immune. Detail in the LOG.

No reviewer dispatched, per the wave prompt.
