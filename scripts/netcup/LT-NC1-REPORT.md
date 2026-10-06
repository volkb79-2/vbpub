# LT-NC1 report: netcup scp-api fixes from the live Phase 0/1 run

Scope: `scp-api.py`, `monitor-task.py`, their tests, README, CLI spec and surface manifest, review catalog.
`install-host.py` and `netcup_scp_client.py` were not touched. Nothing was run against live netcup.
Fixtures in `tests/test_lt_nc1.py` are the response shapes observed live (`P0/`, `P1/` evidence).

Claim boundary: I ran the full local suite (612 tests, see "Verification") and the plants below. Everything else
(live behaviour of the new calls) is untested and is for the controller's live re-test.

## Item -> fix -> test -> plant

Plant = one mutation applied to a scratch copy of the tree, the whole suite run, result "killed" when a test failed.
Runner: `scratchpad/mutate.py` (not committed).

| # | Item | Fix | Tests (in `tests/test_lt_nc1.py` unless noted) | Plant (killed by) |
|---|------|-----|-----|-----|
| 1 | B1 user id is a string | `_scp_user_id` accepts int > 0 or `[0-9]+` string > 0; rejects bool, 0, negatives, non-digits, unicode digits | `test_scp_user_id_accepts_int_and_digit_string`, `..._rejects_everything_else` (16 values), `test_firewall_policies_works_with_live_string_userinfo` | string branch disabled: 3 killed; `<= 0` check dropped: 3 killed |
| 2 | B2 dryrun list | answer is no longer forced to a dict; list/object/None accepted; empty = "snapshot possible", non-empty list = blocking reasons; `--json` raw. Body now carries `diskName`/`onlineSnapshot` | `test_dryrun_empty_answer_means_snapshot_possible`, `..._lists_blocking_reasons`, `..._object_answer_is_rendered`, `..._json_emits_the_raw_response`, `test_dryrun_payload_matches_check_schema` | `_response_dict` restored: 5 killed; reasons rendered as "possible": 1 killed |
| 3 | B3 create 422 | `--disk-name`, `--online`, `--description`; default disk = the only disk from the disks GET, several/none -> `--disk-name` required (exit 2, nothing POSTed). Spec `ServerSnapshotCreate`/`ServerSnapshotCreateCheck` | `test_create_defaults_diskname_to_the_only_disk_and_matches_schema`, `..._with_every_option_matches_schema`, `test_online_snapshot_needs_no_disk_lookup`, `test_disk_name_is_required_unless_exactly_one_disk`, `test_snapshot_options_exist_on_the_real_parser`; replay rows `snapshots-*` | diskName not sent: 11 killed; multi-disk allowed: 1 killed |
| 4 | Error output | verb-raised usage errors (`show_help=True`) are re-raised as message + `Hint: run ./scp-api.py help <verb>` (monitor-task: `--poll` check, same idea). **Banner line and full-help-on-parser-error come from cli-extended: reported, library not patched** (see below) | `test_verb_usage_error_is_message_plus_one_hint_not_full_help`, `test_error_hint_does_not_override_an_explicit_hint`, `tests/test_monitor_task.py::test_watch_rejects_nonpositive_or_nonfinite_poll_before_authentication` | wrapper disabled: 2 killed |
| 5a | `--filter` not on id | `_filter_rows(..., fields)`: imageflavours searches name+alias, iso-bootable name+description | `test_filter_does_not_match_the_id_column`, `test_filter_still_matches_alias_and_iso_description` | all-fields search restored: 2 killed |
| 5b | `tasks <uuid>` local check | UUID regex in the pre-validation block (exit 2, no API call, before auth); bare `tasks cancel` keeps its message | `test_tasks_uuid_is_validated_locally_before_any_call`, `test_tasks_bare_cancel_keeps_its_own_message` | check disabled: 1 killed |
| 5c | `detach --json` | emits `{"detached": true, "serverId": N}` (the DELETE answers 204, spec) | `test_detach_json_emits_json` | plain print restored: 1 killed |
| 5d | `consistent` n/a | plain output shows `consistent: n/a` when null/missing under `--consistency-check`; `--json` keeps the API null | `test_consistency_check_shows_na_when_nothing_assigned`, `..._keeps_a_real_answer` | n/a branch removed: 1 killed |
| 5e | metrics table | compact table: series, unit, min, avg, max, last; unknown shape falls back to JSON; `--json` raw | `test_metrics_plain_is_a_compact_table_with_units`, `..._json_stays_the_raw_map`, `..._unexpected_shape_falls_back_to_json` | JSON dump restored: 10 killed |
| 5f | `tasks --limit 0` | no request, prints "limit 0: nothing requested" (`[]` with `--json`) | `test_tasks_limit_zero_makes_no_request` | check removed: 1 killed |
| 5g | non-monotonic progress | `monitor-task` clamps the shown percent to the highest seen; raw value in `--debug` | `test_monitor_progress_is_clamped_monotonic` (100, 91, 94, 100), `..._notes_raw_value_in_debug`, `..._increasing_values_are_untouched` | clamp removed: 2 killed |
| 5h | detach during attach | `_refuse_if_tasks_active`: GET `/tasks?serverId=&state=PENDING` and `RUNNING`; refuse with the task uuid and a `monitor-task.py watch` hint | `test_detach_is_refused_while_a_task_is_active[PENDING,RUNNING]`, `test_detach_ignores_finished_rows_the_api_may_return` | check removed: 6 killed |
| 6 | boot order | spec has it: `PATCH /api/v1/servers/{id}` merge-patch `ServerBootorderPatch`, read in `serverLiveInfo.bootorder`. New verb `boot-order SERVER_ID [set ORDER]` (mutating, `--yes`, protected-server guard, order validated against the `Bootorder` enum, no duplicates). `attach-iso --change-boot-device-to-cdrom` reads the order first and prints previous order + the exact restore command | `test_boot_order_*` (read, set body vs schema, bad orders, consent, guard, missing order), `test_attach_with_cdrom_boot_prints_the_previous_order_and_restore_command`, `..._without_the_boot_flag_reads_no_boot_order`; 8 catalog rows | body truncated: 4 killed; guard removed: 1 killed; hint text changed: 1 killed; duplicate check removed: 2 killed |
| 7 | `snapshotCount` vs `[]` | no client bug found: the list call is the spec endpoint `GET /servers/{id}/snapshots`, which has no paging/filter parameters, and the live answer was `[]` with HTTP 200. Provider-side; documented in README and the `snapshots` help text | doc only | n/a |

Other fixes made on the way: SA-005 (create/dryrun printed nothing on an empty answer) is closed in
`cli-review-findings-scp-api.toml`; `create` now prints "snapshot requested" for an empty body.

## cli-extended behaviour (item 4), not patched

- Banner: `CliOutput.error()` in `libraries/cli-extended/src/cli_extended/output.py` (lines ~262-270) writes a blank
  line and `identity.headline` after the first error of every run. This is the shared policy
  (`cli_extended/testing.py` asserts the headline is present after usage errors). `monitor-task.py:129` is the same path.
  To remove it the library needs an opt-out; I did not patch it. Suggested backlog entry: cli-extended, "error() banner
  after diagnostics is noise for an operator who just saw the tool name in the command line".
- Parser errors (non-integer id, unknown choice, missing positional) are formatted by `_print_error_with_help` /
  `ExtendedArgumentParser` in `parser.py` and still print the full help; only errors raised by the verbs themselves
  got the one-line hint. Same suggested backlog entry.

## Open points for the reviewer / controller

- Metric units: the spec states none. Network is labelled `B/s*` and packets `pkt/s*`, inferred from the sample sizes against
  `rxMonthlyInMiB`; cpu and disk are shown as `raw`. The table footnote says so. If the controller knows the real units,
  `_METRIC_UNITS` in `scp-api.py` is the single place to change.
- Active-task check scope: it refuses on ANY PENDING/RUNNING task of the server (brief: "check for running tasks first"),
  not only ISO tasks. It relies on the `serverId` filter of `GET /tasks`; live behaviour of that filter with `state` is
  used already by `tasks --server-id`, but the combination was not run live.
- `online` snapshots: sent as `onlineSnapshot: true` without `diskName` per the spec; live acceptance not tested.
- `iso-attached detach` JSON is synthesised (`detached`, `serverId`) because the API returns 204. `rescuesystem deactivate`
  has the same plain-text-only output; left unchanged (not in the brief).
- Behaviour change worth a live check: `snapshots create` and `dryrun` now make one extra GET (`/disks`) unless `--disk-name`/`--online` is given.

## Verification

- Suite: see the commit message and the hand-back for the gate verdict (read in a separate step).
- Regenerated: `cli-surface-scp-api.json`, `CLI-SPEC-scp-api.md` (`cli-extended surface sync`); `surface check` and `audit`
  pass for scp-api, monitor-task and install-host. 11 new catalog rows + 1 re-signed (`snapshots/minimum`); one row's
  expected stdout changed (`metrics 42 cpu --json`: `cpu` -> `CPU0`, the live series name).
- Tests that changed because behaviour changed: detach/attach-iso/snapshots tests in `tests/test_scp_api_explore.py`, `CALLS`
  in `tests/test_cli_cases_scp_api.py`, metrics routes in `tests/case_harness.py` (now the live `{timestamp: {series: n}}` shape),
  the `--poll` refusal assertion in `tests/test_monitor_task.py`.
