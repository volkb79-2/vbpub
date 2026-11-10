## Positive offline Cargo run

Container: `assay-rust-r0-s1-positive-r1`; detached, network `none`, memory 2 GiB, CPU 2, cgroup parent `$CGROUP_PARENT_DEV_GATES`.

Exact launch command:

```sh
docker run -d --name assay-rust-r0-s1-positive-r1 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu; cd /work/topos/sampler; set +e; cargo test --offline --locked > /tmp/probe.stdout 2>/tmp/probe.stderr; probe_status=$?; set -e; printf '\''%s\n'\'' '\''--- stdout ---'\''; cat /tmp/probe.stdout; printf '\''%s\n'\'' '\''--- stderr ---'\''; cat /tmp/probe.stderr; printf '\''probe_exit_status=%s\n'\'' "$probe_status"; exit "$probe_status"'
```

Exact wait/log/remove commands:

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-positive-r1  # positive case only
docker wait assay-rust-r0-s1-positive-r1
docker logs assay-rust-r0-s1-positive-r1
docker rm assay-rust-r0-s1-positive-r1
```

Captured output (Docker logs combines the process stdout/stderr; the in-container wrapper labels both streams):

```text
b52fb1ebbe0c744f492dbf6b10c57aafbc582f9b584d7d8d47fd34b5dcb6360e
none
docker_wait_status=0
--- stdout ---

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
test cmdline::redaction_tests::secret_prefixed_pairs_with_a_colon_are_hidden_whole ... ok
test cmdline::redaction_tests::secret_headers_by_name ... ok
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
test tests::events_request_returns_window_entity_filter_and_limit ... ok
test tests::disk_await_is_not_applicable_without_io ... ok
test tests::hard_events ... ok
test tests::first_sample_zero_delta_and_reset_are_distinguishable ... ok
test tests::limits_are_values_or_typed_states ... ok
test tests::hello_current_status_and_typed_values ... ok
test tests::malformed_and_unknown_requests_get_typed_errors ... ok
test tests::memory_stack_sums_to_total ... ok
test tests::process_scan_reads_io_for_candidates_and_sweeps_for_the_rest ... ok
test tests::per_cpu_percentages_and_host_gauges ... ok
test tests::root_pressure_falls_back_to_proc_and_missing_full_is_typed ... ok
test tests::s01_a_failed_write_is_rolled_back_and_counted ... ok
test tests::responses_are_bounded_and_say_truncated ... ok
test tests::s01_torn_tail_is_cut_at_restart_and_bad_frames_stop_the_reader_and_are_reported ... ok
test tests::s02_a_corrupt_compressed_frame_is_counted_not_fatal ... ok
test tests::s02_a_panic_while_serving_is_an_error_reply_not_a_dead_daemon ... ok
test tests::s02_heavy_requests_are_bounded_and_get_busy ... ok
test tests::s03_the_writer_queue_never_blocks_drops_oldest_and_keeps_dictionary_and_events ... ok
test tests::s05_one_oom_kill_is_one_event_on_the_owning_cgroup ... ok
test tests::s03_a_cmdline_read_that_blocks_does_not_stall_the_tick ... ok
test tests::s08_run_times_are_only_collected_when_a_summary_is_wanted ... ok
test tests::s06_throttle_is_read_from_the_slice_that_carries_the_limits ... ok
test tests::s09_dictionary_is_durable_before_frames_and_names_stay_one_line ... ok
test tests::s10_threads_count_for_d_state_and_a_hidden_d_thread_is_reported_then_found ... ok
test tests::s11_orphans_are_removed_and_the_cap_counts_the_whole_directory ... ok
test tests::s12_a_cgroup_recreated_at_the_same_path_is_a_new_incarnation ... ok
test tests::s14_events_survive_torn_lines_bad_utf8_and_psi_has_hysteresis ... ok
test tests::s15_a_second_instance_is_refused_the_socket ... ok
test tests::s16_files_are_created_private ... ok
test tests::s19_a_huge_window_does_not_wrap ... ok
test tests::s20_memory_stat_counters_are_classified_explicitly ... ok
test tests::s17_p95_of_large_gauges_is_exact_and_never_above_max ... ok
test tests::socket_mode_0660_is_applied_but_bind_stays_private ... ok
test tests::socket_mode_config_default_and_validation ... ok
test tests::socket_rejects_oversized_requests_and_stalled_clients_do_not_block_publishing ... ok
test tests::stacked_devices_unknown_lines_and_io_cost ... ok
test tests::tiers_roll_up_counters_to_sums_gauges_to_four_stats_and_keep_states ... ok
test tests::unparsable_values_are_unavailable_never_zero_and_iowait_clamps ... ok
test tests::view_additions_pids_max_swap_current_and_root_memory_from_meminfo ... ok
test tests::vmstat_keys_the_kernel_lacks_are_typed_states_not_missing ... ok
test tests::x01_a_link_planted_while_running_is_not_followed_by_dictionary_or_segment_writes ... ok
test tests::x01_a_planted_symlink_in_a_data_directory_is_refused_and_its_target_is_never_touched ... ok
test tests::x01_data_directories_must_be_real_private_directories_with_a_safe_parent ... ok
test tests::x01_files_are_opened_nofollow_and_only_when_regular ... ok
test tests::x02_a_second_instance_exits_having_changed_nothing ... ok
test tests::x03_tmpfs_is_checked_warned_about_and_can_be_required ... ok
test tests::x05_the_writer_queue_is_bounded_in_bytes_too ... ok
test tests::slurp_reads_until_eof_because_seq_files_answer_in_chunks ... ok
test tests::x07_secrets_in_command_lines_are_redacted_before_they_leave_the_sampler ... ok
test tests::x08_the_socket_is_never_created_with_a_looser_mode ... ok
test tests::x06_one_stuck_pid_occupies_one_worker_and_is_not_asked_again ... ok
test tests::x09_dictionary_files_carry_a_line_count_that_is_checked_at_load ... ok
test tests::x11_a_hung_sampling_thread_shows_in_status ... ok
test tests::x10_slurp_refuses_files_without_end_and_gives_the_memory_back ... ok
test tests::x13_rollups_say_how_many_ticks_of_a_bucket_held_a_value ... ok
test tests::x14_compact_can_keep_not_applicable_states_and_fields_reports_omitted_entities ... ok
test tests::x15_a_device_that_drops_out_of_io_stat_and_returns_is_a_reset_not_one_huge_delta ... ok
test tests::x12_the_first_stop_signal_is_graceful_and_a_later_one_ends_the_process ... ok
test tests::zswap_falls_back_to_meminfo_with_typed_states ... ok

test result: ok. 81 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.68s

--- stderr ---
   Compiling serde_core v1.0.229
   Compiling proc-macro2 v1.0.107
   Compiling quote v1.0.47
   Compiling unicode-ident v1.0.27
   Compiling zmij v1.0.23
   Compiling syn v3.0.9
   Compiling serde_json v1.0.154
   Compiling winnow v1.0.4
   Compiling serde v1.0.229
   Compiling toml_parser v1.1.5+spec-1.1.0
   Compiling serde_spanned v1.1.2
   Compiling serde_derive v1.0.229
   Compiling toml_datetime v0.7.5+spec-1.1.0
   Compiling winnow v0.7.15
   Compiling twox-hash v2.1.5
   Compiling memchr v2.8.3
   Compiling toml_writer v1.1.3+spec-1.1.0
   Compiling itoa v1.0.18
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
    Finished `test` profile [unoptimized + debuginfo] target(s) in 11.67s
     Running unittests src/bin/r0_cov_probe.rs (target/debug/deps/r0_cov_probe-e618c36820bdeb12)
     Running unittests src/main.rs (target/debug/deps/topos_sampler-de39b6e681deb2a4)
probe_exit_status=0
assay-rust-r0-s1-positive-r1
```

## Missing locked archive

Container: `assay-rust-r0-s1-missing-r1`; detached, network `none`, memory 2 GiB, CPU 2, cgroup parent `$CGROUP_PARENT_DEV_GATES`.

Exact launch command:

```sh
docker run -d --name assay-rust-r0-s1-missing-r1 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu; rm -f /usr/local/cargo/registry/cache/index.crates.io-1949cf8c6b5b557f/serde_json-1.0.154.crate; cd /work/topos/sampler; set +e; cargo test --offline --locked > /tmp/probe.stdout 2>/tmp/probe.stderr; probe_status=$?; set -e; printf '\''%s\n'\'' '\''--- stdout ---'\''; cat /tmp/probe.stdout; printf '\''%s\n'\'' '\''--- stderr ---'\''; cat /tmp/probe.stderr; printf '\''probe_exit_status=%s\n'\'' "$probe_status"; exit "$probe_status"'
```

Exact wait/log/remove commands:

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-missing-r1  # positive case only
docker wait assay-rust-r0-s1-missing-r1
docker logs assay-rust-r0-s1-missing-r1
docker rm assay-rust-r0-s1-missing-r1
```

Captured output (Docker logs combines the process stdout/stderr; the in-container wrapper labels both streams):

```text
b0e23154711b74f90c8db78f130a6d924b39dddb84526a152ec78bfa53d46a72
docker_wait_status=101
--- stdout ---
--- stderr ---
error: failed to download `serde_json v1.0.154`

Caused by:
  attempting to make an HTTP request, but --offline was specified
probe_exit_status=101
assay-rust-r0-s1-missing-r1
```

## Appended byte archive (Cargo unexpectedly accepts it)

Container: `assay-rust-r0-s1-append-byte-r1`; detached, network `none`, memory 2 GiB, CPU 2, cgroup parent `$CGROUP_PARENT_DEV_GATES`.

Exact launch command:

```sh
docker run -d --name assay-rust-r0-s1-append-byte-r1 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu; archive=/usr/local/cargo/registry/cache/index.crates.io-1949cf8c6b5b557f/serde_json-1.0.154.crate; printf x >> "$archive"; cd /work/topos/sampler; set +e; cargo test --offline --locked > /tmp/probe.stdout 2>/tmp/probe.stderr; probe_status=$?; set -e; printf '\''%s\n'\'' '\''--- stdout ---'\''; cat /tmp/probe.stdout; printf '\''%s\n'\'' '\''--- stderr ---'\''; cat /tmp/probe.stderr; printf '\''probe_exit_status=%s\n'\'' "$probe_status"; exit "$probe_status"'
```

Exact wait/log/remove commands:

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-append-byte-r1  # positive case only
docker wait assay-rust-r0-s1-append-byte-r1
docker logs assay-rust-r0-s1-append-byte-r1
docker rm assay-rust-r0-s1-append-byte-r1
```

Captured output (Docker logs combines the process stdout/stderr; the in-container wrapper labels both streams):

```text
f7be44f323838287e060d6f2c906cca095632f3d55187808e4c8366fde39d54d
docker_wait_status=0
--- stdout ---

running 1 test
test tests::inline_test_covers_derived_and_macro_calls ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s


running 81 tests
test cmdline::redaction_tests::a_line_of_exactly_the_cap_plus_its_nul_is_not_cut ... ok
test cmdline::redaction_tests::a_cut_line_never_shows_half_a_secret ... ok
test cmdline::redaction_tests::bare_userinfo_is_visible_only_when_a_short_login ... ok
test cmdline::redaction_tests::every_url_in_a_word_and_bare_tokens_are_hidden ... ok
test cmdline::redaction_tests::game_server_arguments_are_hidden ... ok
test cmdline::redaction_tests::headers_and_user_pairs_are_hidden ... ok
test cmdline::redaction_tests::names_that_only_mention_a_secret_stay_visible ... ok
test cmdline::redaction_tests::nested_secret_pairs_follow_a_url ... ok
test cmdline::redaction_tests::option_names_are_judged_by_whole_parts ... ok
test cmdline::redaction_tests::ordinary_words_stay_readable ... ok
test cmdline::redaction_tests::secret_headers_by_name ... ok
test cmdline::redaction_tests::secret_prefixed_pairs_with_a_colon_are_hidden_whole ... ok
test cmdline::redaction_tests::token_exemptions_are_exact_count_names ... ok
test cmdline::redaction_tests::user_forms_hide_the_password_unless_it_is_an_id_pair ... ok
test tests::a_socket_that_fails_verification_is_unlinked ... ok
test tests::cgroup_view_reports_namespaced_when_self_is_at_root ... ok
test tests::config_validation_rejects_bad_shapes ... ok
test tests::counter_resets_and_is_bottom_do_not_flip_on_a_failed_read ... ok
test tests::current_fields_projection_and_compact_form ... ok
test tests::byte_cap_holds_even_for_one_oversized_segment ... ok
test tests::dictionary_reuses_ids_after_retention_and_survives_reload ... ok
test tests::d_state_count_per_cgroup_and_blkio_delay_when_enabled ... ok
test tests::disk_await_is_not_applicable_without_io ... ok
test tests::events_request_returns_window_entity_filter_and_limit ... ok
test tests::hard_events ... ok
test tests::first_sample_zero_delta_and_reset_are_distinguishable ... ok
test tests::hello_current_status_and_typed_values ... ok
test tests::limits_are_values_or_typed_states ... ok
test tests::malformed_and_unknown_requests_get_typed_errors ... ok
test tests::memory_stack_sums_to_total ... ok
test tests::per_cpu_percentages_and_host_gauges ... ok
test tests::responses_are_bounded_and_say_truncated ... ok
test tests::root_pressure_falls_back_to_proc_and_missing_full_is_typed ... ok
test tests::process_scan_reads_io_for_candidates_and_sweeps_for_the_rest ... ok
test tests::s01_a_failed_write_is_rolled_back_and_counted ... ok
test tests::s01_torn_tail_is_cut_at_restart_and_bad_frames_stop_the_reader_and_are_reported ... ok
test tests::s02_a_corrupt_compressed_frame_is_counted_not_fatal ... ok
test tests::s02_heavy_requests_are_bounded_and_get_busy ... ok
test tests::s02_a_panic_while_serving_is_an_error_reply_not_a_dead_daemon ... ok
test tests::s03_the_writer_queue_never_blocks_drops_oldest_and_keeps_dictionary_and_events ... ok
test tests::s03_a_cmdline_read_that_blocks_does_not_stall_the_tick ... ok
test tests::s05_one_oom_kill_is_one_event_on_the_owning_cgroup ... ok
test tests::s08_run_times_are_only_collected_when_a_summary_is_wanted ... ok
test tests::s06_throttle_is_read_from_the_slice_that_carries_the_limits ... ok
test tests::s09_dictionary_is_durable_before_frames_and_names_stay_one_line ... ok
test tests::s11_orphans_are_removed_and_the_cap_counts_the_whole_directory ... ok
test tests::s10_threads_count_for_d_state_and_a_hidden_d_thread_is_reported_then_found ... ok
test tests::s12_a_cgroup_recreated_at_the_same_path_is_a_new_incarnation ... ok
test tests::s14_events_survive_torn_lines_bad_utf8_and_psi_has_hysteresis ... ok
test tests::s15_a_second_instance_is_refused_the_socket ... ok
test tests::s16_files_are_created_private ... ok
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
test tests::x01_a_link_planted_while_running_is_not_followed_by_dictionary_or_segment_writes ... ok
test tests::x01_a_planted_symlink_in_a_data_directory_is_refused_and_its_target_is_never_touched ... ok
test tests::x01_data_directories_must_be_real_private_directories_with_a_safe_parent ... ok
test tests::x01_files_are_opened_nofollow_and_only_when_regular ... ok
test tests::x02_a_second_instance_exits_having_changed_nothing ... ok
test tests::slurp_reads_until_eof_because_seq_files_answer_in_chunks ... ok
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

test result: ok. 81 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.68s

--- stderr ---
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
   Compiling toml_writer v1.1.3+spec-1.1.0
   Compiling twox-hash v2.1.5
   Compiling itoa v1.0.18
   Compiling winnow v0.7.15
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
    Finished `test` profile [unoptimized + debuginfo] target(s) in 11.59s
     Running unittests src/bin/r0_cov_probe.rs (target/debug/deps/r0_cov_probe-e618c36820bdeb12)
     Running unittests src/main.rs (target/debug/deps/topos_sampler-de39b6e681deb2a4)
probe_exit_status=0
assay-rust-r0-s1-append-byte-r1
```

## Repacked archive with injected compile_error

Container: `assay-rust-r0-s1-repacked-verify-r1`; detached, network `none`, memory 2 GiB, CPU 2, cgroup parent `$CGROUP_PARENT_DEV_GATES`.

Exact launch command:

```sh
docker run -d --name assay-rust-r0-s1-repacked-verify-r1 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu; archive=/usr/local/cargo/registry/cache/index.crates.io-1949cf8c6b5b557f/serde_json-1.0.154.crate; work=/tmp/serde_json-tampered; mkdir -p "$work"; tar -xf "$archive" -C "$work"; printf '\''\ncompile_error!("tampered cached crate");\n'\'' >> "$work/serde_json-1.0.154/src/lib.rs"; tar -czf "$archive.repacked" -C "$work" serde_json-1.0.154; mv "$archive.repacked" "$archive"; cd /work/topos/sampler; set +e; cargo test --offline --locked > /tmp/probe.stdout 2>/tmp/probe.stderr; probe_status=$?; set -e; printf '\''%s\n'\'' '\''--- stdout ---'\''; cat /tmp/probe.stdout; printf '\''%s\n'\'' '\''--- stderr ---'\''; cat /tmp/probe.stderr; printf '\''probe_exit_status=%s\n'\'' "$probe_status"; exit "$probe_status"'
```

Exact wait/log/remove commands:

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-repacked-verify-r1  # positive case only
docker wait assay-rust-r0-s1-repacked-verify-r1
docker logs assay-rust-r0-s1-repacked-verify-r1
docker rm assay-rust-r0-s1-repacked-verify-r1
```

Captured output (Docker logs combines the process stdout/stderr; the in-container wrapper labels both streams):

```text
04463bd4a796bf1514339be86686d34e7b431133c6e8e7d58ea7e758ce08c7d5
docker_wait_status=101
--- stdout ---
--- stderr ---
   Compiling serde_core v1.0.229
   Compiling proc-macro2 v1.0.107
   Compiling quote v1.0.47
   Compiling unicode-ident v1.0.27
   Compiling zmij v1.0.23
   Compiling syn v3.0.9
   Compiling serde_json v1.0.154
   Compiling serde v1.0.229
   Compiling winnow v1.0.4
   Compiling toml_parser v1.1.5+spec-1.1.0
   Compiling serde_spanned v1.1.2
   Compiling toml_datetime v0.7.5+spec-1.1.0
   Compiling serde_derive v1.0.229
   Compiling winnow v0.7.15
   Compiling twox-hash v2.1.5
   Compiling memchr v2.8.3
   Compiling toml_writer v1.1.3+spec-1.1.0
   Compiling itoa v1.0.18
error: tampered cached crate
   --> /usr/local/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/serde_json-1.0.154/src/lib.rs:443:1
    |
443 | compile_error!("tampered cached crate");
    | ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

   Compiling toml v0.9.8
error: could not compile `serde_json` (lib) due to 1 previous error
warning: build failed, waiting for other jobs to finish...
probe_exit_status=101
assay-rust-r0-s1-repacked-verify-r1
```

## Changed Cargo.lock checksum

Container: `assay-rust-r0-s1-lock-r1`; detached, network `none`, memory 2 GiB, CPU 2, cgroup parent `$CGROUP_PARENT_DEV_GATES`.

Exact launch command:

```sh
docker run -d --name assay-rust-r0-s1-lock-r1 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu; cd /work/topos/sampler; sed -i '\''s/e7e9cc8b1b85264074fbcc02a88680c4096b1e47df8f739dceb03bf482f04bd6/0000000000000000000000000000000000000000000000000000000000000000/'\'' Cargo.lock; set +e; cargo test --offline --locked > /tmp/probe.stdout 2>/tmp/probe.stderr; probe_status=$?; set -e; printf '\''%s\n'\'' '\''--- stdout ---'\''; cat /tmp/probe.stdout; printf '\''%s\n'\'' '\''--- stderr ---'\''; cat /tmp/probe.stderr; printf '\''probe_exit_status=%s\n'\'' "$probe_status"; exit "$probe_status"'
```

Exact wait/log/remove commands:

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-lock-r1  # positive case only
docker wait assay-rust-r0-s1-lock-r1
docker logs assay-rust-r0-s1-lock-r1
docker rm assay-rust-r0-s1-lock-r1
```

Captured output (Docker logs combines the process stdout/stderr; the in-container wrapper labels both streams):

```text
62ba881947d900f958e32589144f32f4a1f36963fcb82d64a0cd7e42231b9ef4
docker_wait_status=101
--- stdout ---
--- stderr ---
error: checksum for `serde_json v1.0.154` changed between lock files

this could be indicative of a few possible errors:

    * the lock file is corrupt
    * a replacement source in use (e.g., a mirror) returned a different checksum
    * the source itself may be corrupt in one way or another

unable to verify that `serde_json v1.0.154` is the same as when the lockfile was generated

probe_exit_status=101
assay-rust-r0-s1-lock-r1
```

## Valid build-time manifest precheck

Container: `assay-rust-r0-s1-precheck-valid-r1`; detached, network `none`, memory 2 GiB, CPU 2, cgroup parent `$CGROUP_PARENT_DEV_GATES`.

Exact launch command:

```sh
docker run -d --name assay-rust-r0-s1-precheck-valid-r1 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu; true; set +e; /usr/local/bin/verify-crate-cache > /tmp/probe.stdout 2>/tmp/probe.stderr; probe_status=$?; set -e; printf '\''%s\n'\'' '\''--- stdout ---'\''; cat /tmp/probe.stdout; printf '\''%s\n'\'' '\''--- stderr ---'\''; cat /tmp/probe.stderr; printf '\''probe_exit_status=%s\n'\'' "$probe_status"; exit "$probe_status"'
```

Exact wait/log/remove commands:

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-precheck-valid-r1  # positive case only
docker wait assay-rust-r0-s1-precheck-valid-r1
docker logs assay-rust-r0-s1-precheck-valid-r1
docker rm assay-rust-r0-s1-precheck-valid-r1
```

Captured output (Docker logs combines the process stdout/stderr; the in-container wrapper labels both streams):

```text
6044b586226bfa7452b4163108f496c3f7258955aae7d9c99286e06c6f748b8c
docker_wait_status=0
--- stdout ---
--- stderr ---
probe_exit_status=0
assay-rust-r0-s1-precheck-valid-r1
```

## Missing archive precheck refusal

Container: `assay-rust-r0-s1-precheck-missing-r1`; detached, network `none`, memory 2 GiB, CPU 2, cgroup parent `$CGROUP_PARENT_DEV_GATES`.

Exact launch command:

```sh
docker run -d --name assay-rust-r0-s1-precheck-missing-r1 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu; rm -f /usr/local/cargo/registry/cache/index.crates.io-1949cf8c6b5b557f/serde_json-1.0.154.crate; set +e; /usr/local/bin/verify-crate-cache > /tmp/probe.stdout 2>/tmp/probe.stderr; probe_status=$?; set -e; printf '\''%s\n'\'' '\''--- stdout ---'\''; cat /tmp/probe.stdout; printf '\''%s\n'\'' '\''--- stderr ---'\''; cat /tmp/probe.stderr; printf '\''probe_exit_status=%s\n'\'' "$probe_status"; exit "$probe_status"'
```

Exact wait/log/remove commands:

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-precheck-missing-r1  # positive case only
docker wait assay-rust-r0-s1-precheck-missing-r1
docker logs assay-rust-r0-s1-precheck-missing-r1
docker rm assay-rust-r0-s1-precheck-missing-r1
```

Captured output (Docker logs combines the process stdout/stderr; the in-container wrapper labels both streams):

```text
fa092c85e91304b79347679f68a09c446d1483a9bdbc1ca63490b97bbb5ec2e6
docker_wait_status=1
--- stdout ---
--- stderr ---
ERROR: cached crate is missing: serde_json-1.0.154.crate
probe_exit_status=1
assay-rust-r0-s1-precheck-missing-r1
```

## Tampered archive precheck refusal

Container: `assay-rust-r0-s1-precheck-tampered-r1`; detached, network `none`, memory 2 GiB, CPU 2, cgroup parent `$CGROUP_PARENT_DEV_GATES`.

Exact launch command:

```sh
docker run -d --name assay-rust-r0-s1-precheck-tampered-r1 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local bash -c 'set -eu; archive=/usr/local/cargo/registry/cache/index.crates.io-1949cf8c6b5b557f/serde_json-1.0.154.crate; printf x >> "$archive"; set +e; /usr/local/bin/verify-crate-cache > /tmp/probe.stdout 2>/tmp/probe.stderr; probe_status=$?; set -e; printf '\''%s\n'\'' '\''--- stdout ---'\''; cat /tmp/probe.stdout; printf '\''%s\n'\'' '\''--- stderr ---'\''; cat /tmp/probe.stderr; printf '\''probe_exit_status=%s\n'\'' "$probe_status"; exit "$probe_status"'
```

Exact wait/log/remove commands:

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-precheck-tampered-r1  # positive case only
docker wait assay-rust-r0-s1-precheck-tampered-r1
docker logs assay-rust-r0-s1-precheck-tampered-r1
docker rm assay-rust-r0-s1-precheck-tampered-r1
```

Captured output (Docker logs combines the process stdout/stderr; the in-container wrapper labels both streams):

```text
33d1e4d247f296eac72aaf3fef67819c8d9c261ee1c8b2541e09cfb9259c4aee
docker_wait_status=1
--- stdout ---
--- stderr ---
ERROR: cached crate checksum mismatch: serde_json-1.0.154.crate expected e7e9cc8b1b85264074fbcc02a88680c4096b1e47df8f739dceb03bf482f04bd6 got c68522d2f33f2067c1da57551997d04fa64586eb99423a31fe2cc2ccb74adf6a
probe_exit_status=1
assay-rust-r0-s1-precheck-tampered-r1
```
