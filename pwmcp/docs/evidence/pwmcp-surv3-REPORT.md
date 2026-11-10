<!-- origin: 2026-11-07; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/pwmcp-surv3-REPORT.md -->
<!-- relation: PWMCP R2 survivor closeout; verbatim source text follows -->

# PWMCP `v1.64.1-r4` R2 survivor closeout

Date: 2026-11-07  
Implementation branch: `pwmcp-surv3-20261106`  
Worktree: `/workspaces/vbpub/.worktrees/pwmcp-surv3-20261106`  
R2 campaign commit: `b1659836e323cd3995105d94455c897968fd5ff9`

## Result

The supplied R2 campaign had 493 candidates: 461 killed, 29 survived, and 3 crashed. Assay returned `ERROR/EXEC_FAILED`; the three crashes were recorded as OOM kills. R0/R1 had passed at that campaign baseline.

The closeout adds tests for 25 real survivor gaps and fixes the faulty `read1(size)` test double behind the runaway drain mutant. Four survivors are equivalent for supported inputs and are explicitly waived below. The three crashed candidates now each fail a focused test under a hand-applied mutant. **This was not a rerun of the full R2 campaign**, so 489 killed / 4 equivalent / 0 crashes is the expected classification on a rerun, not a new Assay verdict.

Final R1 gate: `run-gate r1` **PASS** on `46b75eaa43636f9b9606a7c7219fc9792ad91c91`; 2,127/2,127 lines and 642/642 branches covered.

Manual mutation evidence: 28/28 focused probes failed as expected with one hand-applied mutant at a time. The probes ran serially in `tester-unified` through temporary `run-gate` command lanes in a detached scratch worktree. Each source file was restored before the next pytest invocation. Selective pytest calls included `--cli-case-partial`. The temporary lanes and scratch checkout are disposable and are not in the implementation branch.

## Survivor decisions

“Equivalent” means the mutant leaves supported, caller-visible behavior unchanged and no test distinguishes its result. Every other table row names the assertion that fails with the corresponding hand-applied mutant.

| File:line | Mutation | Root cause and decision | Hand-mutant evidence |
|---|---|---|---|
| `client/src/pwmcp_client/blocked.py:95` | `True→False` | Missing immutability assertion for the public verdict value. Fixed with a frozen-dataclass check. | `test_block_verdict_cannot_be_mutated` — **FAILED**, assignment did not raise `FrozenInstanceError`. |
| `client/src/pwmcp_client/blocked.py:120` | `LtE→Lt` | The exact signal clipping boundary was uncovered. Fixed at 80 characters. | `test_signal_at_the_clip_limit_is_preserved_in_full` — **FAILED**, the mutant added an ellipsis at length 80. |
| `client/src/pwmcp_client/blocked.py:126` | `True→False` | No test proved `cf-mitigated: challenge` is definitive without corroboration. | `test_cf_mitigated_challenge_header_is_definitive_by_itself` — **FAILED**, no verdict was returned. |
| `client/src/pwmcp_client/blocked.py:130` | `LtE→Lt` | The maximum accepted challenge-text length was not tested. Fixed at exactly 6,000 characters. | `test_definitive_text_at_the_character_limit_still_counts` — **FAILED**, no verdict was returned. |
| `client/src/pwmcp_client/blocked.py:166` | `LtE→Lt` | The near-empty page boundary was not tested. Fixed at exactly 600 characters. | `test_frame_on_a_page_at_the_near_empty_limit_still_counts` — **FAILED**, no verdict was returned. |
| `client/src/pwmcp_client/blocked.py:175` | `Eq→NotEq` | Existing ranking coverage used a definitive named-provider hit, masking the tie-breaker. | `test_named_provider_outranks_generic_when_both_hits_are_non_definitive` — **FAILED**, mutant selected Generic CAPTCHA instead of Cloudflare. |
| `client/src/pwmcp_client/cli.py:143` | `None→[]` | **Equivalent; waived.** `_observed_user_agent` is private, and its consumer uses `observed_ua or active.user_agent`; both values select the same fallback. |
| `client/src/pwmcp_client/cli.py:171` | `Eq→NotEq` | Console warning messages were not distinguished from errors in a direct test. | `test_console_diagnostics_count_errors_but_not_warnings` — **FAILED**, the warning incremented `console_errors`. |
| `client/src/pwmcp_client/cli.py:182` | `GtE→Gt` | Subresource status 400 was missing from the boundary cases. | `test_a_subresource_status_of_400_is_reported` — **FAILED**, the mutant left the 400 status out. |
| `client/src/pwmcp_client/cli.py:190` | `GtE→Gt` | Main-document status 400 was missing from the boundary cases. | `test_a_main_document_status_of_400_is_reported` — **FAILED**, `summary()` returned `None`. |
| `client/src/pwmcp_client/cli.py:200` | `None→[]` | **Equivalent; waived.** An empty diagnostics summary is only consumed by `if note`; `None` and `[]` both emit nothing. |
| `client/src/pwmcp_client/cli.py:320` | `True→False` | Screenshot attempts were not tested with retries; screenshots do not run the DOM-stability check. | `test_screenshot_is_not_retried_for_dom_stability` — **FAILED**, the mutant navigated and captured twice instead of once. |
| `client/src/pwmcp_client/cli.py:356` | `None→[]` | **Equivalent; waived.** A non-text payload produces no small-result warning, and the caller only emits truthy notes. |
| `client/src/pwmcp_client/cli.py:359` | `Lt→LtE` | The strict half-size threshold had no equality case. | `test_equal_half_size_is_not_reported_as_a_shrunken_result` — **FAILED**, the mutant warned at exactly half size. |
| `client/src/pwmcp_client/cli.py:364` | `Lt→LtE` | The strict 1 KiB shell threshold had no equality case. | `test_exactly_one_kibibyte_is_not_a_small_shell_result` — **FAILED**, the mutant emitted the small-result warning at 1,024 bytes. |
| `client/src/pwmcp_client/cli.py:369` | `None→[]` | The no-warning result is distinguished by exact-`None` assertions in the new threshold tests. | `test_equal_half_size_is_not_reported_as_a_shrunken_result` and `test_exactly_one_kibibyte_is_not_a_small_shell_result` — **FAILED**, both received `[]` instead of `None`. |
| `client/src/pwmcp_client/cli.py:384` | `LtE→Lt` | The documented maximum retry count was not accepted by a test. | `test_the_maximum_retry_count_is_accepted` — **FAILED**, the mutant rejected `--retries 10`. |
| `client/src/pwmcp_client/cli.py:451` | `True→False` | Retry warnings were tested at default verbosity, not with warnings filtered by `--log-level error`. | `test_navigation_retry_warning_survives_error_log_level` — **FAILED**, stderr was empty. |
| `client/src/pwmcp_client/cli.py:461` | `True→False` | Unstable-page retry warnings had the same log-level gap. | `test_unstable_page_retry_warning_survives_error_log_level` — **FAILED**, stderr was empty. |
| `client/src/pwmcp_client/cli.py:469` | `True→False` | Page-diagnostic warnings had the same log-level gap. | `test_page_diagnostic_warning_survives_error_log_level` — **FAILED**, the diagnostics line was suppressed. |
| `client/src/pwmcp_client/cli.py:498` | `True→False` | Storage-state transparency had no quiet-mode assertion. | `test_storage_state_transparency_survives_quiet` — **FAILED**, the storage-state counts were suppressed. |
| `client/src/pwmcp_client/cli.py:566` | `True→False` | Blocked-output refusal was not tested above warning verbosity. | `test_blocked_output_refusal_warning_survives_error_log_level` — **FAILED**, the “was not written” warning was suppressed. |
| `client/src/pwmcp_client/ocr.py:184` | `Or→And` | **Equivalent; waived.** For `urlsplit`, a truthy username or password implies `@` is present in `netloc`; therefore `username or password or "@" in netloc` is identical to `"@" in netloc`, and changing the first `or` to `and` leaves the result unchanged. Existing `https://user@host` and `http://@host:1` cases already cover credential refusal. | No separate test is needed for this equivalent mutation. |
| `client/src/pwmcp_client/storage_state.py:27` | `True→False` | The scoped state value's frozen contract was not asserted. | `test_scoped_state_cannot_be_reassigned` — **FAILED**, assignment did not raise `FrozenInstanceError`. |
| `client/src/pwmcp_client/storage_state.py:66` | `Or→And` | A hand-edited state file may contain an out-of-range port with no hostname. The exception path must return a non-matching sentinel without raising; the existing invalid-port case had a hostname and missed this shape. | `test_hostless_origin_with_out_of_range_port_is_ignored` — **FAILED**, the mutant called `.lower()` on `None` and raised `AttributeError` instead of returning with zero matching origins. |
| `client/src/pwmcp_client/storage_state.py:67` | `Is→IsNot` | Existing coverage checked the count but not which origin matched the default-port target. | `test_default_ports_are_normalised_and_explicit_ports_must_match` — **FAILED**, the mutant selected `https://browser.geekbench.com:8443` instead of the no-port origin. |
| `client/src/pwmcp_client/storage_state.py:91` | `Or→And` | The fallback for an `OSError` with no `strerror` was not tested. | `test_open_error_without_a_strerror_uses_the_readable_fallback` — **FAILED**, the message ended in `None` rather than `unreadable`. |
| `containers/ocr/ocr_service.py:372` | `Gt→Gte` | Scale 1 had no assertion that the image was not resampled. | `test_scale_one_does_not_resample_the_original_frame` — **FAILED**, the mutant called `resize((12, 8), BICUBIC)` at scale 1. |
| `containers/ocr/ocr_service.py:766` | `True→False` | The shutdown test checked thread separation but not daemon status. | `test_main_shuts_down_cleanly_on_sigterm_and_sigint[15]` — **FAILED**, the shutdown thread was not a daemon. |

## Three OOM-crashed candidates

| File:line | Mutation | Root cause and decision | Hand-mutant evidence |
|---|---|---|---|
| `containers/ocr/ocr_service.py:606` | `Or→And` | **Test-double defect, fixed.** The fake `Reader.read1(size)` returned `b"x"` for every size, including zero, and appended every read to a growing list. Under the mutant, an exhausted byte budget reached `read1(0)` repeatedly and grew memory until OOM. The fake now returns `b"x"[:size]`, matching the `read1(size)` contract; zero returns EOF and the assertion catches the extra read cleanly. The fake also stops after eight reads with a clear failure, covering non-progressing mutations outside the lane's operators. | `test_rejection_drain_consumes_only_up_to_its_byte_budget` — **FAILED**, with reads `[1, 0]` instead of `[1]`; no OOM. A hand-mutated `budget -= 0` also fails at the eight-read cap without unbounded growth. |
| `containers/ocr/ocr_service.py:619` | `Eq→NotEq` | **Route comparison; OOM attribution was concurrent.** Reversing the `/v1/engines` route comparison does not allocate without bound. The candidate's resource window overlapped the runaway drain candidate; Assay attributes a positive OOM event to every overlapping candidate window. | `test_engines_lists_names_versions_languages_and_unavailable_reason` — **FAILED**, expected HTTP 200, got 404. |
| `containers/ocr/ocr_service.py:621` | `Eq→NotEq` | **Route comparison; OOM attribution was concurrent.** Reversing the `/v1/ocr` route comparison also has no unbounded allocation path. Its window overlapped the same campaign activity. | `test_routing_errors_are_json[GET-/nope-404-not_found]` — **FAILED**, expected 404/not_found, got 405/method_not_allowed. |

The supplied progress evidence records `oom_kill` delta 1 for each crash window, with `memory.events.max` deltas 41,148 (line 606), 33,203 (line 619), and 979 (line 621). The route mutants' focused failures and the line-606 test-double loop establish the root cause and the clean rejection behavior. Assay documents that resource-limit events are assigned to every overlapping candidate window (`assay/README.md`, R2 candidate resource-event attribution). This is why the 3 GiB lane cap and `assay.toml` were left unchanged; the host limits were not changed.

## Count reconciliation

| Category | Count | Evidence |
|---|---:|---|
| Original R2 killed | 461 | Supplied campaign summary. |
| Previously surviving, now test-killed | 25 | 25 focused hand-mutant failures above. |
| Previously crashed, now test-killed | 3 | 3 focused hand-mutant failures above; the drain mutant no longer OOMs. |
| Equivalent survivors, waived | 4 | Three falsey `None→[]` swaps (`cli.py:143`, `:200`, `:356`) and `ocr.py:184`; rationale recorded in the table. |
| Unresolved crashes after closeout probes | 0 | All three crash mutations fail a focused test without resource exhaustion. |

The arithmetic gives 489 test-killed candidates and 4 equivalent survivors for a future full R2 rerun. No R2 campaign rerun was performed, so these are closeout classifications, not Assay campaign buckets.

## Changed files

- `pwmcp/client/tests/test_blocked.py`
- `pwmcp/client/tests/test_inspect.py`
- `pwmcp/client/tests/test_ocr.py`
- `pwmcp/client/tests/test_storage_state.py`
- `pwmcp/tests/test_ocr_service.py` (R1 timeout-test determinization, fix round)
- `pwmcp/tests/test_ocr_service_hardening.py`
- `pwmcp/KNOWN_ISSUES_TODO_BACKLOG.md` (PWMCP-41)
- `pwmcp/CHANGES.md` (`Unreleased`)

## Fix round — 2026-11-07

Final branch head: `46b75eaa43636f9b9606a7c7219fc9792ad91c91` (`pwmcp-surv3-20261106`). The branch integrated the current local `origin/main` before these fixes. The task branch was not pushed or merged to `main`.

- Added `test_hostless_origin_with_out_of_range_port_is_ignored` for the hand-edited origin `https://:99999`. With `_origin()` manually changed from `hostname or ""` to `hostname and ""`, the test failed with the reviewed `AttributeError` at `.lower()`; after restoring the scratch source, `git diff --exit-code` confirmed it matched the scratch base. The normal/valid-port branch already defaults a missing hostname before `.lower()`; this exception return was the only adjacent path with that failure shape. The test passes against the real code and asserts the origin is ignored while a matching cookie remains available.
- Manually changed `cli.py:369` from `return None` to `return []`. Both exact-`None` threshold tests failed with `[]`, so this is test-killed, not an equivalence waiver. Restored the source and verified its diff was empty.
- Reclassified `ocr.py:184` as equivalent and removed `test_username_only_sidecar_userinfo_is_refused`. A truthy username or password from `urlsplit` implies `@` in `netloc`, making the first `or→and` result unchanged; existing userinfo tests already cover refusal.
- Added an eight-read safety cap to `test_rejection_drain_consumes_only_up_to_its_byte_budget`. With the service manually changed to `budget -= 0`, the probe failed at the cap with its clear message, without unbounded growth.
- The five survivor-closeout test files passed one at a time in `tester-unified`: 310 passed total. The additional timeout regression passed its focused gate (1 passed). All selective pytest runs used `--cli-case-partial`.
- The first R1 run on `c400d6d4c75bb759de72f4f72df66a777bdd9c98` exposed a load-sensitive test that used a 0.4-second real subprocess deadline for both a forced timeout and the follow-up success. Replaced the wall-clock subprocess behavior with a deterministic `_run` stub that raises `TimeoutExpired` once and returns canned TSV next; the same HTTP test still verifies 504 and slot release.
- Final `nice -n 10 run-gate r1` on `46b75eaa43636f9b9606a7c7219fc9792ad91c91`: **PASS**, 2,127/2,127 lines and 642/642 branches.

Corrected closeout counts: 25 formerly surviving candidates now have failing test evidence; 4 are equivalent (`cli.py:143`, `:200`, `:356`, and `ocr.py:184`); all 3 formerly crashed candidates now fail focused tests without resource exhaustion; 0 unresolved crashes. A future full R2 rerun is expected to report 489 killed / 4 equivalent / 0 crashed across 493 candidates. **R2 was not rerun**, so these are classifications, not new Assay campaign buckets.
