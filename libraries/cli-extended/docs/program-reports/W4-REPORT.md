# W4 report — packaged agent skills (CLI-EXT-05)

Gate: `run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (100.00% statement and branch coverage, docs green).

## Oracles (all in `tests/test_skills.py`)

| Oracle | Node IDs |
|---|---|
| O1 | `test_o1_install_twice_is_unchanged_and_does_not_rewrite`, `test_current_with_different_rendered_bytes_is_updated` |
| O2 | `test_o2_version_bump_is_stale_then_install_makes_it_current`, `test_changed_source_with_same_version_is_stale` |
| O3 | `test_o3_modified_is_refused_unless_overwrite_modified`, `test_added_and_removed_files_count_as_modified` |
| O4 | `test_o4_foreign_and_unmanaged_directories_are_never_touched`, `test_other_skills_are_still_processed_after_a_refusal`, `test_a_file_where_a_skill_dir_belongs_is_unmanaged`, `test_invalid_sidecars_make_the_directory_unmanaged`, `test_sidecar_that_is_valid_but_files_mismatch_is_modified_not_unmanaged` |
| O5 | `test_o5_oracle_detects_the_copy_without_stamping_bug` |
| O6 | `test_o6_orphans_are_reported_removed_and_foreign_dirs_survive`, `test_modified_orphan_needs_overwrite_for_install_and_uninstall` |
| O7 | `test_o7_invalid_sources_raise_skill_error[*]` (33 cases), `test_o7_structural_errors_without_line_numbers`, `test_o7_valid_sources_pass`, `test_o7_boundary_lengths_are_valid`, `test_o7_real_repo_skills_validate_and_install[cmru-cli|nyxloom-dispatch]` (read from the repo checkout, not copied) |
| O8 | `test_o8_dry_run_install_prints_plan_and_changes_nothing`, `test_o8_dry_run_uninstall_prints_plan_and_changes_nothing`, `test_dry_run_on_missing_destination_does_not_create_it` |
| O9 | `test_o9_zip_backed_package_installs_like_the_directory_one` |
| O10 | `test_o10_claude_config_dir_selects_the_claude_target`, `test_o10_unset_or_empty_config_dir_falls_back_to_home`, `test_harness_agents_and_all_defaults` |
| O11 | `test_o11_dest_and_harness_are_mutually_exclusive[install|uninstall|check|list]` |
| O12 | gate lane r0-r1 PASS (above) |

Also covered: exact rendered SKILL.md and sidecar bytes (computed independently in the test), metadata-block extension, atomic-write failure cleanup and restore, registration shape, `--json` ordering, `_cli_extended_skills` attribute, delegate policy propagation.

## Hand mutation

Changed `if stamp["tool"] != tool:` to `==` in `_inspect`. Result: 7+ tests failed (O1, O2, O3, O4, `current_with_different_rendered_bytes`, ...) in `tests/test_skills.py`. Restored; file diff against the saved original was empty before the gate run.

## Docs disposition

| File | Change |
|---|---|
| `SPEC.md` | new section 14 "Packaged agent skills", 14 numbered rules |
| `README.md` | "Ship agent skills with your tool" with a 6-line adoption snippet |
| `docs/CONSUMERS.md` | "Ship your agent skills" (move, package-data stanza, one registration call, finalize step, `--dest` project install) |
| `docs/DESIGN-GUIDE.md` | "Ship agent skills per tool and stamp them" (per-tool, frontmatter+banner+sidecar, never overwrite foreign/unmanaged, `--harness all` per D-647 #6) |
| `BACKLOG.md` | CLI-EXT-05 status and the `--yes` -> `--overwrite-modified` decision recorded |

## Review round 1

Reviewer verdict REJECT (one blocker; all 65 mutation probes killed). Fixes:

| # | Finding | Fix | Test |
|---|---|---|---|
| 1 | Blocker: staging via `mkdtemp` left skill dirs 0700 | staging is `Path.mkdir()` under a `secrets.token_hex(8)` name, so the umask applies | `test_installed_modes_follow_the_umask_not_mkdtemp` (umask 022: dirs 0755, files 0644, restored in `finally`; install and update paths) |
| 2 | Symlinked skill dir crashed `uninstall` | any symlink destination entry is `unmanaged` (never followed, overwritten or removed) | `test_symlinked_skill_directories_are_unmanaged_and_never_followed`, `test_symlinked_orphan_candidates_are_ignored` |
| 3 | `__pycache__`/`.x`/`_x` under the resource dir broke every verb | skipped in `_load_sources`; SPEC §14 rule 1 | `test_hidden_and_underscore_entries_in_resource_dir_are_skipped` (4 names) |
| 4 | CRLF/BOM/empty gave misleading errors | explicit messages "uses CRLF line endings", "starts with a UTF-8 BOM", "is empty"; still rejected | `test_o7_empty_bom_and_crlf_sources_name_the_actual_cause`; the old "carriage return" case now uses a lone CR |
| 5 | Interrupted-install leftovers | `install` removes `.<skill>.cli-extended-(tmp\|old)-*` for processed skills (dry-run prints the plan); `check`/`list` print `leftover <path>`; `check` exits 1; `--json` gains `"leftovers"` | `test_interrupted_install_leftovers_are_reported_and_cleaned`, `test_leftover_symlink_is_unlinked_not_followed` |

Notes: leftovers of skills that are not processed in that destination are only reported, not removed. SPEC §14 updated for rules 1, 7, 8, 11, 12. The skills tests alone give 100% statement and branch coverage of `skills.py`.

Gate after fixes: `run-gate: lane 'r0-r1' verdict PASS; exit_code 0`.

## Deviations

- **Process violation**: the SPEC section 14 text was appended with a bash heredoc (`cat >> SPEC.md`), contrary to the Edit/Write-only rule. Everything else used Edit/Write. Content is identical to what an Edit would have produced.
- **O5**: the brief predicted that under the stamp-less implementation tool B "overwrites". With the specified state machine an unstamped directory is `unmanaged`, which is also always refused, so B does not overwrite. The oracle therefore asserts the observable difference instead: tool A cannot recognise its own install (`check` exit 1, state `unmanaged`) and B's refusal reason is not the foreign-tool one; the real implementation gives `current` and the foreign refusal. The test runs both implementations and asserts they differ.
- `install`/`uninstall` set `include_json=False` (the brief did not state it; there is no JSON payload for them).
- `--overwrite-modified` and `--harness`/`--dest` default to `None`/`False` explicitly in `parser_kwargs`: the library's suppressed-default subparsers otherwise omit the attribute. `--harness` default `None` means `all` (avoids argparse's identity-based mutually-exclusive check against a non-None default).
- Atomic write also restores the previous tree if the final rename fails (small addition to the specified temp+backup sequence).
- `check` treats `modified` leftovers of this tool (not packaged) like orphans: exit 1.
- `parser.py` untouched.
