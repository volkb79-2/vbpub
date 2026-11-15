# Hand-mutation record for the M1 `model` and `proto` crates

Rust R2 (a `cargo-mutants` campaign judged by assay) is **not adopted yet** — it is R-3b,
pending in [`assay/docs/RUST-METHODOLOGY.md`](../../assay/docs/RUST-METHODOLOGY.md), and
`tester-unified-rust` carries no `cargo-mutants`. The methodology's substitute until then
is a **hand mutation campaign**, recorded here rather than in git-ignored evidence, so the
claim survives the worktree.

## Method

One mutation at a time, on the working tree, in the lane image only:

```sh
docker run --rm --network none --cgroup-parent "$CGROUP_PARENT_DEV_GATES" \
  -v <host-repo>:<host-repo> -w <host-repo>/.worktrees/<branch>/hostobs \
  tester-unified-rust:local \
  bash -c 'CARGO_TARGET_DIR=/tmp/t tools/cargo-offline.sh test -p hostobs-model -p hostobs-proto --offline --locked'
```

Each step is: (1) record the file's SHA-256, (2) apply one textual mutation, (3) run the
two crates' tests, (4) classify the outcome (`killed` = a test failed; `not built` = the
mutation does not type-check, which is not a kill), (5) restore the file, (6) assert the
restored digest equals the recorded one. **No mutation is left in the tree, and no
restored file differs by one byte from the committed source.** A `SURVIVED` row is a test
gap, so each of those was answered by adding the test that kills it and re-running the
mutation.

Twenty-one mutations were applied; all are killed. Numbers below are the campaign's
verdict counts, not a coverage proxy.

## Verdicts

| # | Mutation | Verdict | Tests that failed |
|---|---|---|---|
| 1 | `rate`: zero-interval boundary moved by one nanosecond | killed | `a_zero_interval_yields_no_rate_at_all`, `every_surface_of_a_derivation_is_reachable_and_typed` |
| 2 | `rate`: reset boundary widened to an equal reading | killed | `an_equal_reading_is_a_real_zero_delta_and_not_an_absence`, `the_counter_boundaries_classify_exactly` |
| 3 | `rate`: reversed-interval boundary widened | killed | `a_zero_interval_yields_no_rate_at_all` |
| 4 | `rate`: missed-sample count off by one | killed | `a_missed_sample_widens_the_interval_instead_of_inflating_one_bucket`, `a_one_second_interval_over_the_real_interval_is_a_per_second_delta`, `missed_sample_counting_is_floor_division_and_saturates` |
| 5 | `rate`: zero-nominal guard moved to one | killed | `missed_sample_counting_is_floor_division_and_saturates` |
| 6 | `identity`: retirement after one absent scan instead of two | killed | four: `a_cgroup_missing_one_scan_survives_it`, `one_absent_scan_is_not_deletion_and_two_are`, `retired_keys_can_be_replaced_in_their_slot_without_id_reuse`, `labels_bind_to_active_incarnations_only_and_replace_by_kind` |
| 7 | `model::quality`: measurement predicate inverted | killed | nineteen (every rate, value and absence case) |
| 8 | `frame`: frame-size bound excludes the largest legal frame | killed | `a_frame_of_exactly_the_maximum_size_is_accepted` |
| 9 | `frame`: only future section versions refused, older accepted | killed | `a_known_section_with_an_older_version_is_refused_too` |
| 10 | `frame`: over-long rows body accepted | killed | `every_length_and_flag_refusal_names_the_section_it_is_about` |
| 11 | `frame`: only the first column reference checked | killed | `a_dangling_reference_in_a_later_column_is_refused_too`, `an_empty_block_is_a_legal_fully_declared_snapshot` |
| 12 | `frame`: sectioned flag test inverted | killed | eighteen (every block decode) |
| 13 | `frame`: reserved section-flag mask widened | killed | `every_length_and_flag_refusal_names_the_section_it_is_about` |
| 14 | `reading`: trailing bytes only checked when padding was consumed | killed | `pad_to_end_requires_a_section_body_to_end_exactly_after_its_padding` |
| 15 | `reading`: `u64` read as big-endian | killed | seven, including `every_scalar_reader_consumes_its_bytes_and_refuses_a_short_buffer` |
| 16 | `writing`: `u64` written big-endian | killed | seven, including `integers_are_little_endian_and_scalars_append_in_order` |
| 17 | `dictionary`: aggregation-mask predicate inverted | killed | fourteen |
| 18 | `dictionary`: retirement keeps every entry | killed | `a_new_generation_compacts_and_renumbers_while_dropping_unreachable_entries`, `retiring_everything_keeps_an_empty_generation_with_a_new_number` |
| 19 | `dictionary`: generation not written | killed | ten |
| 20 | `frame`: block dictionary generation not written | killed | nine |
| 21 | `proto::quality`: measurement predicate inverted | killed | `values_keep_their_declared_type_their_bits_and_their_quality`, `codes_round_trip_and_every_outside_code_is_refused` |

Earlier probes in the same campaign that were killed but are not counted above (they
duplicated a boundary already covered): a `<=` reset predicate, `!=`-vs-`>` section
versions, big-endian `i64` write, big-endian `u64` read, a reversed `u64` equality, a
narrowed counter aggregation mask, a rate stored as `stale`, a wrong-type rate mapped to
`zero-interval`, retirement keeping every entry, absent rounds never accumulating and
rounds never advancing.

## What the campaign changed in the code

Two mutations **survived at first** and produced the tests this package added:

- `frame`: `body.len() != expected` → `<` for the rows body (an over-long body accepted).
  The parser already refused it via the frame's trailing-byte rule, so the outcome was
  right and the *message* was wrong — the added case
  `every_length_and_flag_refusal_names_the_section_it_is_about` now pins which section and
  which lengths are reported, and the same over-long case is repeated for columns and the
  dictionary.
- `reading`: trailing bytes only checked when padding was consumed. Answered by
  `pad_to_end_requires_a_section_body_to_end_exactly_after_its_padding`, including the
  already-aligned body carrying bytes past its content.

Three survivor-class gaps were found and closed by adding tests rather than by mutating
again: only the first column reference checked (now a later-column and later-row case), an
exclusive `>` frame-size comparison (now an exactly-at-the-bound case), and an exclusive
`!=` section-version comparison (now an older-version case).

## Not covered by this campaign

- **Equivalent mutants are not enumerated.** Nothing here claims a mutant set was
  complete: the campaign targeted the classifier boundaries and the codec's bounds, as
  the brief scoped, not every operator in the two crates.
- **CPU, allocation and wall-clock behaviour** are untouched: that is the deferred bench in
  `HOB-2`, and a timing assertion would violate the estate rule that a test result may not
  depend on host load.
- **`cargo-mutants` remains the real R2 mechanism** (R-3b). When it lands, this table is
  evidence about test quality, not a release-gate R2 pass.
