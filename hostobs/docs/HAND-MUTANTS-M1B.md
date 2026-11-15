# Hand-mutation record for M1 part 2 (`proto`, Python reference, `kernel`)

Rust R2 (a `cargo-mutants` campaign judged by assay) is **not adopted yet** — it is R-3b in
[`assay/docs/RUST-METHODOLOGY.md`](../../assay/docs/RUST-METHODOLOGY.md), and
`tester-unified-rust` carries no `cargo-mutants`. The substitute is a hand mutation
campaign, recorded here rather than in git-ignored evidence so the claim survives the
worktree (the M1 campaign for `model`/`proto` is [`HAND-MUTANTS-M1.md`](HAND-MUTANTS-M1.md)).

## Method

Every mutation is a **stored patch** in a replayable script, not prose:

```sh
python3 -I docs/hand-mutants/m1b-mutants.py            # the full campaign
python3 -I docs/hand-mutants/m1b-mutants.py --dry-run   # the patches only
```

Each step: record the file's SHA-256, replace one exact fragment, run the affected suite,
classify the outcome, restore the file (in a `finally`, so a crashed measurement cannot
leave a mutation in the tree), and assert the restored digest equals the original.

* Rust steps run in the lane image — `tester-unified-rust:local`, `--network none`, placed
  on the gates cgroup tier — with `tools/cargo-offline.sh test -p hostobs-<package> --offline
  --locked`. The host mount is READ from the cockpit's own bind mounts (the estate's
  dual-mount rule), never defaulted.
* Python steps run `python3 -I -m unittest discover -s python/tests`.

A mutation whose source does not type-check is reported as **`NOT-BUILT`** and counted as
unanswered, never as a kill: it proves nothing about the tests. (The M1 campaign's prose
record did fold "does not build" into "killed", which is exactly the false-certification
shape this estate's rules warn about; the script fixes it here.)

## Verdicts

Twenty-six patches were applied; all twenty-six are killed, and no patch was left in the
tree (every restore is SHA-256-verified).

| # | Finding | Mutation | Verdict | Tests that failed |
|---|---|---|---|---|
| 1 | A1 | decode_block stops requiring FrameType::Block | killed | frame::tests::a_block_shape_is_refused_under_any_other_frame_type |
| 2 | A2 | the decoder's canonical-order check disabled | killed | frame::tests::a_reordered_directory_is_refused_on_the_read_path |
| 3 | A2 | canonical_rank puts Dictionary after Rows | killed | frame::tests::a_block_decoded_from_bytes_with_rows_and_time_is_refused; frame::tests::a_block_shape_is_refused_under_any_other_frame_type; frame::tests::a_column_kind_that_contradicts_the_dictionary_is_refused |
| 4 | A1 | Python: the decoder's frame-type check removed | killed | FAIL: test_every_fixture_round_trips_in_the_reference_codec (test_reference.WireFixtures.test_every_fixture_round_trips_in_the_reference_codec) (case='block-as-req'); FAIL: test_the_fixture_set_pins_every_new_guard (test_reference.WireFixtures.test_the_fixture_set_pins_every_new_guard) (case='block-as-req') |
| 5 | A2 | Python: the canonical-order check removed | killed | FAIL: test_every_fixture_round_trips_in_the_reference_codec (test_reference.WireFixtures.test_every_fixture_round_trips_in_the_reference_codec) (case='section-order'); FAIL: test_the_fixture_set_pins_every_new_guard (test_reference.WireFixtures.test_the_fixture_set_pins_every_new_guard) (case='section-order') |
| 6 | A3 | Python: the directory read unbounded again (struct.error returns) | killed | ERROR: test_a_truncated_directory_is_a_typed_refusal_and_never_a_struct_error (test_reference.WireFixtures.test_a_truncated_directory_is_a_typed_refusal_and_never_a_struct_error); ERROR: test_every_fixture_round_trips_in_the_reference_codec (test_reference.WireFixtures.test_every_fixture_round_trips_in_the_reference_codec) (case='truncated-directory'); ERROR: test_the_fixture_set_pins_every_new_guard (test_reference.WireFixtures.test_the_fixture_set_pins_every_new_guard) (case='truncated-directory') |
| 7 | B5 | cgroup: an unknown topology is counted as a bottom device (the sampler's old bug) | killed | an_io_aggregate_over_bottom_devices_is_a_sum_and_an_unknown_device_is_a_state |
| 8 | B5 | cgroup: a stacked device's counters are summed too | killed | an_io_aggregate_over_bottom_devices_is_a_sum_and_an_unknown_device_is_a_state |
| 9 | B6 | cgroup: `memory.max`'s `max` becomes the number 0 | killed | a_max_limit_is_a_state_and_never_a_number |
| 10 | B2 | cgroup: an unparsable counter is published as zero instead of unreadable | killed | a_parse_failure_is_unreadable_and_never_a_zero |
| 11 | B2 | cgroup: an unparsable io counter still yields a partial total | killed | an_unparsable_io_counter_makes_the_sum_unavailable_and_a_cost_gauge_is_separate |
| 12 | B6 | cgroup: an absent controller file reports `unsupported` | killed | a_whole_cgroup_directory_is_read_file_by_file_in_one_committed_order |
| 13 | B3 | cmdline: a long line is returned whole instead of cut at a word | killed | cmdline::tests::a_cut_line_never_shows_half_a_secret; cmdline::tests::a_long_line_is_cut_at_a_word_and_says_so |
| 14 | B3 | cmdline: the redactor forgets that the next word is an option's value | killed | cmdline::tests::a_cut_line_never_shows_half_a_secret; cmdline::tests::game_server_arguments_are_hidden; cmdline::tests::names_that_only_mention_a_secret_stay_visible |
| 15 | B3 | cmdline: a vanished or empty command line becomes an empty string | killed | cmdline::tests::a_refusal_is_never_an_empty_string_and_never_a_zero |
| 16 | B2 | procfs: a missing status field is published as zero | killed | procfs::tests::a_missing_status_field_is_unsupported_and_a_bad_one_is_unreadable |
| 17 | B1 | procfs: a pid listing keeps any directory name | killed | procfs::tests::a_pid_listing_is_bounded_sorted_and_keeps_only_numbers |
| 18 | B4 | identity: an upper-case boot id is accepted as the lower-case one | killed | identity::tests::a_boot_id_has_two_spellings_and_refuses_everything_else |
| 19 | B4 | identity: a starttime of 0 is accepted (a bare pid becomes an identity) | killed | identity::tests::a_process_identity_needs_a_boot_a_pid_and_a_start_tick |
| 20 | B4 | identity: device 0 is accepted as an identity | killed | identity::tests::a_cgroup_identity_is_its_metadata_and_two_inodes_at_one_path_are_two_cgroups |
| 21 | B2 | parse: a negative unsigned field wraps instead of being refused | killed | identity::tests::a_short_or_malformed_stat_line_is_a_typed_refusal; parse::tests::a_signed_number_keeps_its_sign_and_takes_the_extremes; parse::tests::an_unsigned_number_accepts_digits_and_refuses_a_sign_a_space_and_an_overflow |
| 22 | B2 | value: a `u64` accessor answers for an unmeasured reading | killed | value::tests::a_measurement_reads_back_and_an_absence_never_does |
| 23 | B2 | value: `missing(ok)` is accepted | killed | value::tests::ok_cannot_describe_an_absence |
| 24 | C2 | clock: an interval across a reboot/epoch boundary is subtracted anyway | killed | clock::tests::readings_across_a_restart_are_refused_and_never_subtracted |
| 25 | C2 | clock: a wall jump forwards is the exact tolerance test (>= instead of >) | killed | a_zero_length_interval_refuses_a_rate_instead_of_reporting_infinity |
| 26 | C1 | SP-2: a recreated cgroup at one path keeps the earlier identity | killed | a_cgroup_recreated_at_one_path_is_two_incarnations_and_the_path_says_nothing |

Every row names at least one failing test. The earlier draft of this campaign ran with a
test-name extractor that only understood libtest's `---- name stdout ----` header, so six
rows read `-` while the suite had in fact failed; the script now reads nextest's
`FAIL [...]` lines and libtest's `test ... FAILED` lines too, and the `-` column is
reserved for a run that failed without naming a test at all (none in this campaign). The
full log is the worktree's `logs/pi-hostobs-m1b-mutants.log`.

## What each class covers

**A — the three luna cross-model findings.** `A1`/`A2` mutate the Rust frame-type and
canonical-order guards; `A2`'s third patch changes the rank table itself (so the *encoder*
would also emit a non-canonical order); `A1`/`A2`/`A3` mutate the Python decoder (seeded
`if False:` checks and an unbounded directory read). Every one is killed by the fixture
suite, which is the point: the fixtures pin *behaviour* across two implementations, not
just a Rust function's return value.

**B — the `kernel` collectors.** The five sampler behaviours the plan requires to change:
an unknown topology counted as bottom (`B5`, twice — the guard and the bottom predicate), a
`max` limit turned into a number (`B6`), an absent controller reported as `unsupported`
(`B6`), an unparsable counter published as zero and an unparsable `io.stat` counter still
yielding a partial sum (`B2`) — plus the bounds (a command line returned whole instead of
cut at a word, a vanished process becoming an empty string), the redaction rule that hides
the next word after `--password`, a `status` field zeroed instead of `unsupported`, a
listing that accepts a non-numeric directory name, the three identity zero-field refusals,
the negative-unsigned parse, and the two `Reading` guards.

**C — SP-2.** A cross-boot/epoch interval subtracted anyway, the forward-jump tolerance
boundary moved to `>=`, and the recycled-inode fixture mutated to keep the old identity.

## What this does *not* claim

Not every line of `kernel` is mutation-covered: the campaign mutates the guards this
package argues are load-bearing, not every branch. Twenty-six is a sample of the behaviour
the plan changes, chosen per finding, and the real mechanism stays `cargo-mutants` judged by
assay once R-3b lands.
