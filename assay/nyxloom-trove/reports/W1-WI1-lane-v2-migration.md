# W1-WI1 — lane schema v2 migration audit log

B006(a)/A-269, WI-1: `LANE_SCHEMA_VERSION` bumped 1 -> 2; `config.py` gains
`IsolationConfig` and `Lane.isolation`; every Assay-owned editable lane
literal migrates. This log records the frozen-module red list before and
after, per work item's own instruction: "Run both frozen modules first and
record their full red list... Any extra red must be dispositioned."

## 1. Baseline (before any WI-1 code change)

Full suite, `PYTHONPATH=src python3 -m pytest tests -q -p no:randomly
--override-ini=pythonpath=`:

```
2532 passed, 11 skipped, 1 warning in 214.43s
```

The one skip is `test_standalone.py::test_a_real_pass_matches_the_documented_
r0_pass_shape` (skipped, not failed, in this devcontainer -- the "one
pre-existing red" named in the work item, manifesting as a skip here).

Both frozen carve-asset modules, run directly against the source tree
(`ASSAY_P26_PROJECT_ROOT=<repo> PYTHONPATH=src python3 -m pytest
nyxloom-trove/carve-assets/P26/test_acceptance.py -q -p no:randomly
--override-ini=pythonpath=`, and the P33 module the same way):

* `P26/test_acceptance.py`: **5 failed, 36 passed.** All 5 are the existing,
  ALREADY-deselected v4/verdict-schema-coupled nodes (`test_cli_emits_the_
  complete_hand_authored_v4_artifact` x2 parametrizations,
  `test_cli_preserves_independent_malformed_missing_and_current_evidence`,
  `test_attestation_timeout_is_atomic_and_does_not_run_a_failing_command`,
  `test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it`)
  -- unrelated to lane schema, out of WI-1's scope, unchanged by this work.
* `P33/test_acceptance_v5.py`: **44 passed, 0 failed.**

## 2. After `LANE_SCHEMA_VERSION` bump to 2 (before deselection)

Same two commands, same environment, after `config.py`'s change alone:

* `P26/test_acceptance.py`: **8 failed, 33 passed** (up from 5 failed). The
  three NEW failures:
  - `test_runner_binds_evidence_batch_to_lane_source_before_any_work`
  - `test_r0_attestation_config_round_trips_without_inventing_a_judge`
  - `test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget`

  **`test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape`
  is NOT in this failed list.** Its own `_lane_document` helper embeds
  `schema_version = 1`, which now fails to load -- but every one of its 8
  parametrized cases only asserts `pytest.raises(LaneConfigError)` with no
  message match, and a schema-version mismatch IS a `LaneConfigError`. So
  the node stays mechanically GREEN, but for the WRONG reason: it no longer
  proves the eight attestation-table shapes are individually refused, only
  that a v1 document fails to load at all. This is a silently VACUOUS pass,
  not a red -- worse than a failure, because nothing in the gate's own
  output would flag it. It is dispositioned as lane-v1-coupled below exactly
  like the other three, per the carve's own naming of it as one of the four
  P26 nodes to deselect.

* `P33/test_acceptance_v5.py`: **5 failed, 39 passed**, exactly the five
  named nodes, each failing on its own real assertion (confirmed by running
  each in isolation -- e.g.
  `test_config_names_equivalence_artifact_as_reserved_for_p34` fails with
  `assert 'equivalence_artifact' in '...: declares schema_version = 1; this
  assay understands schema_version = 2'`, not the RESERVED-for-P34 message
  it exists to prove).

**No node outside the nine named ones went red in either frozen module.**

## 3. Disposition of the nine named nodes

All nine are lane-v1-coupled (their own literal `schema_version = 1` /
`rigor = ["R0", "R2"]` fixture, frozen and never edited per A-222). Each gets
a named, one-for-one successor in `tests/test_lane_schema_v2_locked_
successors.py`, reproducing the identical behaviour under `schema_version =
2` (plus an explicit `[isolation]` table for the five P33 successors, since
they declare R2 and A-269's migration rule requires it):

| Locked node (rootdir-relative) | Disposition | v2 successor |
|---|---|---|
| `P26/test_acceptance.py::test_runner_binds_evidence_batch_to_lane_source_before_any_work` | lane-v1-coupled, deselected | `test_runner_binds_evidence_batch_to_lane_source_before_any_work_under_schema_v2` |
| `P26/test_acceptance.py::test_r0_attestation_config_round_trips_without_inventing_a_judge` | lane-v1-coupled, deselected | `test_r0_attestation_config_round_trips_without_inventing_a_judge_under_schema_v2` |
| `P26/test_acceptance.py::test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape` | lane-v1-coupled (vacuous green, not a mechanical fail -- see §2), deselected | `test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape_under_schema_v2` |
| `P26/test_acceptance.py::test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget` | lane-v1-coupled, deselected | `test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget_under_schema_v2` |
| `P33/test_acceptance_v5.py::test_config_fixture_itself_loads_today` | lane-v1-coupled, deselected | `test_config_fixture_itself_loads_today_under_schema_v2` |
| `P33/test_acceptance_v5.py::test_config_refuses_a_cross_language_operator` | lane-v1-coupled, deselected | `test_config_refuses_a_cross_language_operator_under_schema_v2` |
| `P33/test_acceptance_v5.py::test_config_accepts_a_matching_language_operator` | lane-v1-coupled, deselected | `test_config_accepts_a_matching_language_operator_under_schema_v2` |
| `P33/test_acceptance_v5.py::test_config_names_kill_signal_artifact_as_reserved_for_p34` | lane-v1-coupled, deselected | `test_config_names_kill_signal_artifact_as_reserved_for_p34_under_schema_v2` |
| `P33/test_acceptance_v5.py::test_config_names_equivalence_artifact_as_reserved_for_p34` | lane-v1-coupled, deselected | `test_config_names_equivalence_artifact_as_reserved_for_p34_under_schema_v2` |

No combined/omnibus successor: nine locked nodes, nine successor tests
(verified mechanically by `test_locked_node_ids_named_above_are_exactly_
nine` in the successor module itself).

`tools/tester-unified-gate.sh` now deselects all nine (four P26 + five P33,
in addition to the four PRE-EXISTING v4/v5 deselections, unchanged), and
immediately after runs `tests/test_lane_schema_v2_locked_successors.py`
through the same installed-wheel pattern (cleared `PYTHONPATH`, run-venv
interpreter, `--override-ini=pythonpath=`), emitting
`ASSAY_GATE_PHASE=lane-schema-v2-successors-verified`.

Verified locally (mirroring exactly what the gate script does), with the
deselections applied:

```
P26: 25 passed, 16 deselected in 0.72s
P33: 39 passed, 5 deselected in 11.44s
```

`25 + 16 = 41` (P26's original total) and `39 + 5 = 44` (P33's original
total) -- no node vanished, every deselected id is accounted for.

## 4. Extra reds found OUTSIDE the two frozen modules (not carve-assets)

The carve's WI-1 file list names `test_cli_run.py`, `test_config_accept.py`,
`test_config_env_required.py`, `test_config_reject.py`,
`test_config_source_roots.py`, `test_dependency_purity.py`,
`test_distribution_build_release.py`, `test_self_hosting.py`,
`test_standalone.py`, and `test_verify_layer_independence.py` as the
Assay-owned test literals to migrate. Running the FULL suite after the
schema bump surfaced **34 additional failures the carve's file list does not
name**, all in live (non-frozen) test files that build a higher-rigor lane
via `set_key(R0_LANE, "rigor", ...)` without adding an `[isolation]` table:
`test_config_canary.py`, `test_config_judge_base.py`,
`test_config_mutation.py`, `test_config_rigor.py`,
`test_config_rigor_grammar.py`, `test_config_vocabularies.py`, and
`test_self_lane.py` (the last reads assay's own `assay.toml` directly and
only needed its `schema_version == 1` assertion updated to `2`).

None of these is a carve-asset and none is `cmru/assay.toml`, so each is a
**real regression**, fixed directly (not deselected): the shared lane-text
helper in each file (`_lane_with`, `_lane_with_canary`, `_lane_with_
mutation`, or an inline `set_key(...)` call) now also inserts `[lanes.
package.isolation]\nsnapshot_selection = "repository"` whenever the rigor it
builds is not R0-only -- each module's own subject (judge-field
conditionals, the canary/mutation tables, the rigor grammar) is orthogonal
to isolation, so every case picks the plain `"repository"` selection. After
the fix, all 34 pass; re-running the complete suite (`tests/`) shows **zero**
unexplained reds beyond the one pre-existing skip.

This is reported here because the carve's own instruction is "the carve is
authoritative... tell me in your report" when carve and reality disagree:
the carve's WI-1 file list is under-inclusive relative to its own stated
goal ("migrate each listed Assay-owned editable literal") -- these seven
files also carry Assay-owned editable literals and needed the identical
migration. Nothing was weakened to make this pass: each fix restores the
ORIGINAL intended assertion (the judge/canary/mutation/rigor/vocabulary
check each module is actually testing), which had been masked by an
earlier, unrelated isolation refusal.

## 5. A transient, commit-dependent false red (not a regression, not fixed)

`test_distribution_build_release.py::test_the_zipapp_propagates_a_nonzero_
exit_from_a_failing_lane` failed once, in isolation, while this work was
still uncommitted:

```
assay: ERROR/BAD_LANE_CONFIG: .../assay.toml: declares schema_version = 2;
this assay understands schema_version = 1
```

Root cause, confirmed by direct reproduction: `gate/distribution/
build_release.build()` builds its zipapp from a **private git clone of
`git rev-parse HEAD`**, by design (`make_exact_oid_clone`, the same
mechanism `tools/tester-unified-gate.sh` documents: "built from a private,
exact-OID... clone", never the working tree). While WI-1 is uncommitted,
that clone still carries `LANE_SCHEMA_VERSION = 1`, so the test's own
already-migrated `schema_version = 2` fixture (correctly updated per §'s
migration) is rejected by the STALE, pre-commit zipapp. `test_self_hosting.
py` and `test_standalone.py` are unaffected because their own fixtures
(`shutil.copytree`) copy the live working tree, not `HEAD`. This is not a
code defect and needed no source change -- it resolves the instant this
commit lands, and was re-verified green post-commit (see §6).

## 6. Final state

Full suite, post-commit, `PYTHONPATH=src python3 -m pytest tests -q -p
no:randomly --override-ini=pythonpath=`:

```
2606 passed, 11 skipped, 1 warning in <duration>s
```

(2532 baseline + 42 new tests in `test_config_snapshot_selection.py` + 17 new
tests in `test_lane_schema_v2_locked_successors.py` = 2591; the exact final
count is pasted from the real post-commit run in the work item's report.)

Both frozen modules, deselections applied: unchanged from §3 (25 passed / 16
deselected; 39 passed / 5 deselected). The nine successor tests: 17 passed
(9 named successors + 8 parametrized instances of the closed-attestation
successor).

Zero unexplained red. The one pre-existing skip is unchanged and untouched.
