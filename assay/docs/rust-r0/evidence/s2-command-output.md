# S2 raw command and output

Run in detached container assay-rust-r0-s2-final-raw-20261110 with --network none, --memory 2g, --cpus 2, and parent $CGROUP_PARENT_DEV_GATES.

Exact launch command (the command body is passed as the bash -c argument):

```sh
docker run -d --name assay-rust-r0-s2-final-raw-20261110 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu
cd /work/topos/sampler
set +e
cargo llvm-cov --all-targets --offline --locked --lcov --output-path /tmp/sampler-lcov.info > /tmp/llvm.stdout 2>/tmp/llvm.stderr
llvm_status=$?
set -e
printf '\''cargo_llvm_cov_exit_status=%s\n'\'' "$llvm_status"
printf '\''%s\n'\'' '\''--- cargo llvm-cov stdout ---'\''
cat /tmp/llvm.stdout
printf '\''%s\n'\'' '\''--- cargo llvm-cov stderr ---'\''
cat /tmp/llvm.stderr
if [ "$llvm_status" -ne 0 ]; then exit "$llvm_status"; fi
set +e
cargo llvm-cov report --summary-only > /tmp/summary.stdout 2>/tmp/summary.stderr
summary_status=$?
set -e
printf '\''cargo_llvm_cov_report_exit_status=%s\n'\'' "$summary_status"
printf '\''%s\n'\'' '\''--- report stdout ---'\''
cat /tmp/summary.stdout
printf '\''%s\n'\'' '\''--- report stderr ---'\''
cat /tmp/summary.stderr
if [ "$summary_status" -ne 0 ]; then exit "$summary_status"; fi
set +e
PYTHONPATH=/opt/assay/src python3 - <<'\''PY'\'' > /tmp/parser.stdout 2>/tmp/parser.stderr
from pathlib import Path
import json
from assay.coverage_parsers.lcov import parse

trace = Path("/tmp/sampler-lcov.info").read_text()
profile = parse(trace, producer=None)
files = profile.files
probe = files["/work/topos/sampler/src/bin/r0_cov_probe.rs"]
print(json.dumps({
    "files_accepted": len(files),
    "absolute_keys": all(Path(key).is_absolute() for key in files),
    "excluded_all_none": all(item.excluded is None for item in files.values()),
    "branches_all_empty": all(item.branches is not None and not item.branches.by_line for item in files.values()),
    "probe_executed": sorted(probe.executed),
    "probe_missing": sorted(probe.missing),
    "keys": sorted(files),
}, indent=2))
PY
parser_status=$?
set -e
printf '\''assay_parser_exit_status=%s\n'\'' "$parser_status"
printf '\''%s\n'\'' '\''--- assay parser stdout ---'\''
cat /tmp/parser.stdout
printf '\''%s\n'\'' '\''--- assay parser stderr ---'\''
cat /tmp/parser.stderr
if [ "$parser_status" -ne 0 ]; then exit "$parser_status"; fi
printf '\''%s\n'\'' '\''BEGIN_LCOV_RAW'\''
cat /tmp/sampler-lcov.info
printf '\''%s\n'\'' '\''END_LCOV_RAW'\''
sha256sum /tmp/sampler-lcov.info
printf '\''%s\n'\'' '\''--- SF records ---'\''
grep '\''^SF:'\'' /tmp/sampler-lcov.info
printf '\''%s\n'\'' '\''--- zero branch marker counts ---'\''
printf '\''BRF:0='\''; grep -c '\''^BRF:0$'\'' /tmp/sampler-lcov.info
printf '\''BRH:0='\''; grep -c '\''^BRH:0$'\'' /tmp/sampler-lcov.info
exit 0'
```

The container then ran docker wait assay-rust-r0-s2-final-raw-20261110, docker logs assay-rust-r0-s2-final-raw-20261110, and docker rm assay-rust-r0-s2-final-raw-20261110, in that order. Docker wait returned 0. The cargo command, report command, and assay parser each returned 0. The cat output for the generated lcov file is preserved in s2-sampler-lcov.info. The original container file ended without a terminal LF; this committed text copy adds the conventional final LF. The recorded SHA-256 is for the original container file, before that one-byte documentation normalization; its extracted SF/marker lines follow below.

Captured output, excluding only the separately committed full lcov body:

```text
75ee9b57c75c0096ebe1d6fb7e7908fa6d6f1c0772d186f87f83fc651bb128ea
docker_wait_status=0
cargo_llvm_cov_exit_status=0
--- cargo llvm-cov stdout ---

running 1 test
test tests::inline_test_covers_derived_and_macro_calls ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s


running 81 tests
test cmdline::redaction_tests::a_line_of_exactly_the_cap_plus_its_nul_is_not_cut ... ok
test cmdline::redaction_tests::a_cut_line_never_shows_half_a_secret ... ok
test cmdline::redaction_tests::bare_userinfo_is_visible_only_when_a_short_login ... ok
test cmdline::redaction_tests::every_url_in_a_word_and_bare_tokens_are_hidden ... ok
test cmdline::redaction_tests::headers_and_user_pairs_are_hidden ... ok
test cmdline::redaction_tests::game_server_arguments_are_hidden ... ok
test cmdline::redaction_tests::names_that_only_mention_a_secret_stay_visible ... ok
test cmdline::redaction_tests::nested_secret_pairs_follow_a_url ... ok
test cmdline::redaction_tests::option_names_are_judged_by_whole_parts ... ok
test cmdline::redaction_tests::ordinary_words_stay_readable ... ok
test cmdline::redaction_tests::secret_headers_by_name ... ok
test cmdline::redaction_tests::secret_prefixed_pairs_with_a_colon_are_hidden_whole ... ok
test cmdline::redaction_tests::user_forms_hide_the_password_unless_it_is_an_id_pair ... ok
test cmdline::redaction_tests::token_exemptions_are_exact_count_names ... ok
test tests::a_socket_that_fails_verification_is_unlinked ... ok
test tests::cgroup_view_reports_namespaced_when_self_is_at_root ... ok
test tests::config_validation_rejects_bad_shapes ... ok
test tests::counter_resets_and_is_bottom_do_not_flip_on_a_failed_read ... ok
test tests::current_fields_projection_and_compact_form ... ok
test tests::byte_cap_holds_even_for_one_oversized_segment ... ok
test tests::d_state_count_per_cgroup_and_blkio_delay_when_enabled ... ok
test tests::dictionary_reuses_ids_after_retention_and_survives_reload ... ok
test tests::disk_await_is_not_applicable_without_io ... ok
test tests::events_request_returns_window_entity_filter_and_limit ... ok
test tests::hard_events ... ok
test tests::first_sample_zero_delta_and_reset_are_distinguishable ... ok
test tests::limits_are_values_or_typed_states ... ok
test tests::hello_current_status_and_typed_values ... ok
test tests::memory_stack_sums_to_total ... ok
test tests::malformed_and_unknown_requests_get_typed_errors ... ok
test tests::per_cpu_percentages_and_host_gauges ... ok
test tests::process_scan_reads_io_for_candidates_and_sweeps_for_the_rest ... ok
test tests::responses_are_bounded_and_say_truncated ... ok
test tests::s01_a_failed_write_is_rolled_back_and_counted ... ok
test tests::root_pressure_falls_back_to_proc_and_missing_full_is_typed ... ok
test tests::s01_torn_tail_is_cut_at_restart_and_bad_frames_stop_the_reader_and_are_reported ... ok
test tests::s02_a_corrupt_compressed_frame_is_counted_not_fatal ... ok
test tests::s02_a_panic_while_serving_is_an_error_reply_not_a_dead_daemon ... ok
test tests::s02_heavy_requests_are_bounded_and_get_busy ... ok
test tests::s03_the_writer_queue_never_blocks_drops_oldest_and_keeps_dictionary_and_events ... ok
test tests::s03_a_cmdline_read_that_blocks_does_not_stall_the_tick ... ok
test tests::s06_throttle_is_read_from_the_slice_that_carries_the_limits ... ok
test tests::s08_run_times_are_only_collected_when_a_summary_is_wanted ... ok
test tests::s05_one_oom_kill_is_one_event_on_the_owning_cgroup ... ok
test tests::s09_dictionary_is_durable_before_frames_and_names_stay_one_line ... ok
test tests::s11_orphans_are_removed_and_the_cap_counts_the_whole_directory ... ok
test tests::s10_threads_count_for_d_state_and_a_hidden_d_thread_is_reported_then_found ... ok
test tests::s12_a_cgroup_recreated_at_the_same_path_is_a_new_incarnation ... ok
test tests::s14_events_survive_torn_lines_bad_utf8_and_psi_has_hysteresis ... ok
test tests::s16_files_are_created_private ... ok
test tests::s15_a_second_instance_is_refused_the_socket ... ok
test tests::s17_p95_of_large_gauges_is_exact_and_never_above_max ... ok
test tests::s20_memory_stat_counters_are_classified_explicitly ... ok
test tests::s19_a_huge_window_does_not_wrap ... ok
test tests::socket_mode_0660_is_applied_but_bind_stays_private ... ok
test tests::socket_mode_config_default_and_validation ... ok
test tests::socket_rejects_oversized_requests_and_stalled_clients_do_not_block_publishing ... ok
test tests::stacked_devices_unknown_lines_and_io_cost ... ok
test tests::tiers_roll_up_counters_to_sums_gauges_to_four_stats_and_keep_states ... ok
test tests::unparsable_values_are_unavailable_never_zero_and_iowait_clamps ... ok
test tests::view_additions_pids_max_swap_current_and_root_memory_from_meminfo ... ok
test tests::vmstat_keys_the_kernel_lacks_are_typed_states_not_missing ... ok
test tests::slurp_reads_until_eof_because_seq_files_answer_in_chunks ... ok
test tests::x01_a_planted_symlink_in_a_data_directory_is_refused_and_its_target_is_never_touched ... ok
test tests::x01_data_directories_must_be_real_private_directories_with_a_safe_parent ... ok
test tests::x01_files_are_opened_nofollow_and_only_when_regular ... ok
test tests::x01_a_link_planted_while_running_is_not_followed_by_dictionary_or_segment_writes ... ok
test tests::x02_a_second_instance_exits_having_changed_nothing ... ok
test tests::x05_the_writer_queue_is_bounded_in_bytes_too ... ok
test tests::x03_tmpfs_is_checked_warned_about_and_can_be_required ... ok
test tests::x07_secrets_in_command_lines_are_redacted_before_they_leave_the_sampler ... ok
test tests::x08_the_socket_is_never_created_with_a_looser_mode ... ok
test tests::x06_one_stuck_pid_occupies_one_worker_and_is_not_asked_again ... ok
test tests::x09_dictionary_files_carry_a_line_count_that_is_checked_at_load ... ok
test tests::x11_a_hung_sampling_thread_shows_in_status ... ok
test tests::x10_slurp_refuses_files_without_end_and_gives_the_memory_back ... ok
test tests::x13_rollups_say_how_many_ticks_of_a_bucket_held_a_value ... ok
test tests::x14_compact_can_keep_not_applicable_states_and_fields_reports_omitted_entities ... ok
test tests::x15_a_device_that_drops_out_of_io_stat_and_returns_is_a_reset_not_one_huge_delta ... ok
test tests::zswap_falls_back_to_meminfo_with_typed_states ... ok
test tests::x12_the_first_stop_signal_is_graceful_and_a_later_one_ends_the_process ... ok

test result: ok. 81 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.69s

--- cargo llvm-cov stderr ---
info: cargo-llvm-cov currently setting cfg(coverage); you can opt-out it by passing --no-cfg-coverage
   Compiling serde_core v1.0.229
   Compiling proc-macro2 v1.0.107
   Compiling quote v1.0.47
   Compiling unicode-ident v1.0.27
   Compiling zmij v1.0.23
   Compiling syn v3.0.9
   Compiling winnow v1.0.4
   Compiling serde v1.0.229
   Compiling serde_json v1.0.154
   Compiling toml_parser v1.1.5+spec-1.1.0
   Compiling serde_spanned v1.1.2
   Compiling toml_datetime v0.7.5+spec-1.1.0
   Compiling serde_derive v1.0.229
   Compiling memchr v2.8.3
   Compiling twox-hash v2.1.5
   Compiling itoa v1.0.18
   Compiling winnow v0.7.15
   Compiling toml_writer v1.1.3+spec-1.1.0
   Compiling toml v0.9.8
   Compiling ruzstd v0.9.1
   Compiling topos-sampler v0.1.0 (/work/topos/sampler)
warning: field `ts_ms` is never read
  --> src/collect.rs:25:9
   |
24 | pub struct Event {
   |            ----- field in this struct
25 |     pub ts_ms: u64,
   |         ^^^^^
   |
   = note: `#[warn(dead_code)]` (part of `#[warn(unused)]`) on by default

warning: `topos-sampler` (bin "topos-sampler" test) generated 1 warning
    Finished `test` profile [unoptimized + debuginfo] target(s) in 11.97s
     Running unittests src/bin/r0_cov_probe.rs (target/llvm-cov-target/debug/deps/r0_cov_probe-e618c36820bdeb12)
     Running unittests src/main.rs (target/llvm-cov-target/debug/deps/topos_sampler-de39b6e681deb2a4)

    Finished report saved to /tmp/sampler-lcov.info
cargo_llvm_cov_report_exit_status=0
--- report stdout ---
Filename                      Regions    Missed Regions     Cover   Functions  Missed Functions  Executed       Lines      Missed Lines     Cover    Branches   Missed Branches     Cover
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
bin/r0_cov_probe.rs                27                 6    77.78%           4                 2    50.00%          19                 6    68.42%           0                 0         -
cfg.rs                            101                17    83.17%           9                 3    66.67%          67                11    83.58%           0                 0         -
cmdline.rs                        802                28    96.51%          54                 9    83.33%         352                11    96.88%           0                 0         -
collect.rs                       2289               123    94.63%          52                 2    96.15%         983                64    93.49%           0                 0         -
dict.rs                           342                26    92.40%          25                 0   100.00%         182                15    91.76%           0                 0         -
fsafe.rs                          580               109    81.21%          43                14    67.44%         252                37    85.32%           0                 0         -
main.rs                           477               471     1.26%          19                18     5.26%         227               222     2.20%           0                 0         -
procs.rs                          783               126    83.91%          32                 6    81.25%         451                79    82.48%           0                 0         -
selfmon.rs                        321                41    87.23%          19                 2    89.47%         140                21    85.00%           0                 0         -
serve.rs                         1054               147    86.05%          47                 7    85.11%         525                72    86.29%           0                 0         -
store.rs                         1994               231    88.42%         102                13    87.25%        1014               130    87.18%           0                 0         -
util.rs                           329                52    84.19%          35                 3    91.43%         209                33    84.21%           0                 0         -
writer.rs                         248                80    67.74%          27                15    44.44%         114                33    71.05%           0                 0         -
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
TOTAL                            9347              1457    84.41%         468                94    79.91%        4535               734    83.81%           0                 0         -
--- report stderr ---
assay_parser_exit_status=0
--- assay parser stdout ---
{
  "files_accepted": 13,
  "absolute_keys": true,
  "excluded_all_none": true,
  "branches_all_empty": true,
  "probe_executed": [
    21,
    22,
    23,
    24,
    25,
    26,
    35,
    36,
    37,
    38,
    39,
    40,
    41
  ],
  "probe_missing": [
    17,
    18,
    19,
    28,
    29,
    30
  ],
  "keys": [
    "/work/topos/sampler/src/bin/r0_cov_probe.rs",
    "/work/topos/sampler/src/cfg.rs",
    "/work/topos/sampler/src/cmdline.rs",
    "/work/topos/sampler/src/collect.rs",
    "/work/topos/sampler/src/dict.rs",
    "/work/topos/sampler/src/fsafe.rs",
    "/work/topos/sampler/src/main.rs",
    "/work/topos/sampler/src/procs.rs",
    "/work/topos/sampler/src/selfmon.rs",
    "/work/topos/sampler/src/serve.rs",
    "/work/topos/sampler/src/store.rs",
    "/work/topos/sampler/src/util.rs",
    "/work/topos/sampler/src/writer.rs"
  ]
}
--- assay parser stderr ---
[full raw stdout from cat /tmp/sampler-lcov.info is s2-sampler-lcov.info]
675706fb0a5165e72b523837c83f8c248c51c9e03e3d3a76894a778b37030141  /tmp/sampler-lcov.info
--- SF records ---
SF:/work/topos/sampler/src/bin/r0_cov_probe.rs
SF:/work/topos/sampler/src/cfg.rs
SF:/work/topos/sampler/src/cmdline.rs
SF:/work/topos/sampler/src/collect.rs
SF:/work/topos/sampler/src/dict.rs
SF:/work/topos/sampler/src/fsafe.rs
SF:/work/topos/sampler/src/main.rs
SF:/work/topos/sampler/src/procs.rs
SF:/work/topos/sampler/src/selfmon.rs
SF:/work/topos/sampler/src/serve.rs
SF:/work/topos/sampler/src/store.rs
SF:/work/topos/sampler/src/util.rs
SF:/work/topos/sampler/src/writer.rs
--- zero branch marker counts ---
BRF:0=13
BRH:0=13
assay-rust-r0-s2-final-raw-20261110
```
