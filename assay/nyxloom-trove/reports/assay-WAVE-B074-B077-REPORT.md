# assay wave B074 + B077 — implementer REPORT (acceptance boxes)

Branch: `feat/assay-b074-b077-quickwins-2026-09-08`
Gate-verified commit: **`15258dfc85688f03efe23cfdeed1a265b1f5dfa2`** (fix round)
Registered gate `tester-unified`: **PASS (exit 0)**, verified from
`scratchpad/b074-b077-gate3.log` in a separate read, never a piped exit code.
Narrative and rulings: `assay-WAVE-B074-B077-LOG.md` beside this file.

**Revised after round-1 adversarial review** (`assay-WAVE-B074-B077-REVIEW-round1.md`,
ACCEPT-conditional, 3 blockers + 1 decision ask). Rows changed by that round
are marked ▲. The controller's ruling on the decision ask — **extend the flag
to R2's declared-target gate** — is now a row in B074's box rather than a note
in the LOG.

---

## B074 — `judge.allow_test_path_targets`

| # | acceptance line (verbatim from the entry) | status | evidence |
|---|---|---|---|
| 1 ▲ | a `whole_target` lane naming `tests/<...>/lib.py` WITH the flag set is JUDGED, reaching a real `PASS`/`FAIL` on its coverage floor rather than `BAD_LANE_CONFIG` | **MET at LANE level** | `test_lane_allow_test_path_targets.py::test_a_lane_with_the_flag_judges_the_target_and_passes` — a real `assay.toml` through `cli.main(["run", …, "--verdict-json", …])`, asserting exit 0, `outcome == "PASS"`, and the R1 claim's own `executable == 2 / covered == 2 / considered == 1` read back from the verdict it wrote. **Kills the `evaluate_r1`→`evaluate_targets` mutant** (verified: 3 failed). Unit-level corroboration (a real `PASS` *and* a real `FAIL` on the floor): `test_the_flag_reaches_a_real_pass_on_the_coverage_floor`, `…_a_real_fail_…`. |
| 2 ▲ | the SAME lane WITHOUT the flag still refuses `BAD_LANE_CONFIG`, naming the target and the test-path gate | **MET at LANE level** | `test_the_same_lane_without_the_flag_refuses_bad_lane_config` — the same `assay.toml` differing by exactly one line, through `cli.main`; asserts non-zero exit, `BAD_LANE_CONFIG`, the target path, and the remedy in stderr. Unit-level twins retained: `test_the_same_target_without_the_flag_still_refuses`, `test_evaluate_targets_without_the_flag_refuses_the_same_target`. |
| 3 | a `changed_lines` lane over a diff touching that same file still SKIPS it | **MET** | `test_the_changed_line_sweep_still_skips_the_same_file`: `considered == 0`, `executable == 0`, no `files_missing_coverage`, and a `read_source_text` that raises if the skipped path is ever read. Strongest form: the test also asserts `evaluate_coverage` has no such parameter at all, so there is no argument a caller could pass to change it. |
| 4 ▲ | a target that is a genuine test file still refuses even WITH the flag | **MET — the open fork is ruled, and now enforced at BOTH tiers** | `test_a_genuine_test_file_still_refuses_with_the_flag[test_lib.py]` / `[conftest.py]`; `test_a_test_filename_outside_a_test_directory_also_refuses_with_the_flag` (`src/conftest.py`); and, new, R2's own half in `test_the_r2_gate_itself_refuses_a_test_path_target_without_the_flag`. The per-adapter split is now **derived from `cli._built_in_registry().entries`** with a completeness guard (`test_every_registered_adapter_has_a_split_case`), so a fifth adapter without a case turns the suite red rather than narrowing the "in every adapter" claim (round-1 Blocker 3, A-270). Ruling: LOG § "the two open forks, decided" (a). |
| 5 ▲ | the flag appears in the verdict's resolved judgment | **MET at LANE level, with a documented call** | `test_the_verdict_records_the_effective_policy` — reads `judgment.r1.allow_test_path_targets is True` out of a verdict a real lane run wrote. **Kills the `_run_prepared_lane`→`JudgmentR1` mutant** (verified: 2 failed), which the previous unit-level evidence did not. `test_a_lane_that_did_not_opt_in_emits_no_such_key_and_still_verifies` pins the additive claim at the same level: the exact pre-B074 key set, and `assay verify` exit 0. Additive, **no `VERDICT_SCHEMA_VERSION` bump** — reasoning and the in-version precedent (`5b2730b6`) in LOG § (b). |
| 6 ▲ | *(controller ruling on round-1's decision ask — not in the original box)* the flag reaches R2's declared-target gate too | **MET** | `runner._mutation_targets_whole` now honours the same flag on the same terms, importing `evaluate._is_test_filename` rather than reproducing the split. `test_an_r1_r2_lane_with_the_flag_resolves_the_target_at_BOTH_tiers`: R1 `PASS` with the policy recorded, R2 no longer `BAD_LANE_CONFIG` and reaching a real mutation judgment with `candidate_count >= 1` generated *from* the declared test-path target. **Kills the third forwarding mutant** (verified: 1 failed). Controlled negative: `test_an_r1_r2_lane_without_the_flag_refuses_at_both_tiers`. |

### Binding constraints from the wave prompt

| constraint | status |
|---|---|
| Shape 1 (flag), not Shape 2 (drop the veto) | **held** — the veto stands by default and for every non-declaring path. |
| `evaluate.py:428`'s sweep-side check untouched | **held** — unmodified; acceptance 3 proves the behaviour. |
| `mutation.py:470/478`'s check untouched | **held** — unmodified. That is `resolve_mutation_targets`, the changed-line R2 sweep; it takes no such parameter. Distinct from `runner._mutation_targets_whole`, the DECLARED-target gate the controller ruled into scope (row 6). |
| the other four `_resolve_whole_target` gates unchanged and still applying with or without the flag | **held, and tested WITH the flag set** — five tests: symlink, source-root containment, regular-file/directory, excluded-directory, adapter-recognised-source. Their five counterparts in R2's twin are likewise unchanged. |
| `assay verify` unaffected by B074 | **held for every pre-B074 artifact** — `test_a_verdict_without_the_flag_still_verifies_unchanged`. `verify` was *extended* to accept the new optional key (required: the schema and the reconstructor are two independent gates and `_reconstruct_judgment_r1`'s own rule is that a new field is registered in the same commit) and to refuse it under `changed_lines` mode or spelled as an explicit `false`. |
| `docs/CONSUMERS.md` gets the flag with a worked, paste-able example | **held** — new subsection with a full lane file, validated by `test_docs_examples_and_vocabulary.py`, which loads every live example through the real `load_lane_file`. Row 4 of its "what it does not do" table was **rewritten** in the fix round: R2 now honours the flag, so the old "it does not reach R2 — declare such a lane R1-only" guidance would have been actively wrong. `docs/DESIGN-GUIDE.md` also gained the flag's own section (review N1), matching `require_branch` and `base_source`. |
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

## Round-1 review disposition

| finding | disposition | where |
|---|---|---|
| **Blocker 1** (HARD) — both runner forwardings mutable to `False` with the whole suite green | **FIXED** — new `tests/test_lane_allow_test_path_targets.py` drives real lanes through `cli.main`; `make_r1_judge` gained the field so runner-level tests can express it at all. **All three forwarding mutants re-applied in place and confirmed killed** (M1 3 failed, M2 2 failed, M3 1 failed). | rows 1, 5, 6 |
| **Blocker 2** (MEDIUM) — R2 refused with a message naming neither the flag nor a remedy, contradicting a policy the same verdict recorded | **FIXED, and superseded by the ruling** — R2 no longer refuses an opted-in target at all; both of its remaining messages name the flag, in R1's own words. | row 6 |
| **Decision ask** — should the flag reach `runner._mutation_targets_whole`? | **RULED by the controller: yes, extend it.** Implemented; my original scope call ("mutating is a different claim from measuring") is withdrawn, and the LOG's old § "Deliberately NOT touched" rewritten rather than left standing. | row 6 |
| **Blocker 3** (LOW) — per-adapter split hand-copied while the prose claims "every adapter, and any future one" | **FIXED** — case set derived from `cli._built_in_registry().entries` with a completeness guard; Go's absent directory branch recorded as an explicit `None` and asserted, not skipped. | row 4 |
| **N1** — no `DESIGN-GUIDE.md` entry | **FIXED** — new section, following `require_branch`'s. | — |
| **N2** — schema description posed the field as "was a target relaxed" | **FIXED** — reworded to the DECLARED/effective policy in the shipped schema and its locked W7 twin together, matching the dataclass comment. | — |
| **N3** — the stronger additive argument | **FOLDED IN** although marked controller-owned: a pre-B074 loader refuses the key as surplus judge config, so no older assay can emit it *whatever* its targets. Now the load-bearing clause in `verdict.py`, the schema description and `CHANGES.md`. | — |
| **N4** (B074/B075 comment collision), **N5** (backlog ticks) | **NOT MINE** — controller-owned per the fix brief. | — |

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
