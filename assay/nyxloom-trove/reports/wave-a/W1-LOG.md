# W1 (B124 + B125) implementer log

Base SHA: `762b500d` (branch `wave-a-w1-retire`, merged with landing incl. W3).
Collect-only before: 5975. After step 1: 5882.

## Path re-resolution (W3 moved files)
Root: `test_python_qualification`, `test_gate_qualify_cmru_b006a`, `test_gate_harness_version_pins`, `test_runner_snapshot_selection`, `test_self_lane`, `test_distribution_gate`, `test_verdict_conformance`, `test_lane_schema_v2_locked_successors`.
Moved to `tests/core/`: `test_config_reject`, `test_b105_mutation_boundaries`, `test_liveness_resources`, `test_mutation_progress_budget_plan`.

## Steps
1. Code (Work 1-7, 12-14): deletions, gate script, assay.toml, tests, nyxloom.toml, ROOT_PINNED. Commit `bb696f53`.
2. Docs (README, DESIGN-GUIDE §13/§15 and the B105 paragraph, CONSUMERS), CHANGES, decisions (A-475/A-477 annotations), backlog, REPORT, this LOG. Commit: the one containing this line (see final message).

## Full suite (once, at the end, nice/ionice, serial)
`pytest tests --ignore=tests/test_self_hosting.py`: 5864 passed, 11 skipped, 1 failed in 376 s. The failure is `tests/core/test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused`, the same environmental `/tmp/.git` baseline failure recorded in W3-LOG (A-040); not caused by W1.

## Deviations
- Backlog B124/B125 read `DONE (W1, merge hash pending ...)`: the merge hash does not exist yet; the controller fills it at merge.
- O1's single-quoted `echo` variant was not run separately; the absence check is a bare-substring check, and the double-quoted variant went red.
- `test_lane_schema_v2_locked_successors.py` docstring notes that the P33 originals no longer run (per Scope), rows kept as history.
- `tests/core/test_mutation_progress_budget_plan.py`: the state-record `-1` case is a parametrization (`delta` in `[1000, -1]`) of the existing test, so the same rerun assertion applies to both.

## Oracles (positive / deliberate break, break never committed)
- O1 positive: `test_gate_script_preserves_required_markers_and_hardens_the_build` passes; absence check uses bare substrings for all 7 retired markers plus `qualify_topos.py`, `qualify_cmru_b006a.py`, `carve-assets/P33`, `carve-assets/W`. Break: added `echo "ASSAY_GATE_PHASE=topos-qualified"` (double-quoted variant) after `run_self_hosted_lane` in `run_inner` -> red (`test_distribution_gate.py:252`); reverted. The single-quoted variant is caught by the same bare-substring check (not run separately).
- O2: the brief's `git grep` prints nothing.
- O3 positive: verdict (4 params), lane (3 params), shard (`-1`, `0`), state record (`-1`), resource (`-1`) all pass. Breaks (each applied, red, reverted; `git diff -- src` empty afterwards):
  - `verify.py:3028` `!=` -> `>`: params 0, 3, 12 (VERDICT-1) red.
  - `config.py:1480` `!=` -> `>`: params 0 and LANE-1 red.
  - `mutation.py:1801` `!=` -> `>`: both new shard cases (`-1`, `0`) red.
  - `liveness_resources.py:179-180` current-side `!=` -> `>`: the new `-1` assert red.
  - `mutation.py` state-record: inserted `if key == "schema_version" and payload[key] < expected: continue` -> the `-1` param red (`assert [] != []`).
- O4 positive: `test_self_lane.py` passes with `deselected == set()` and `not any(a == "--deselect" or a.startswith("--deselect") ...)` for both B105 lanes. Break: added `"--deselect", "tests/x.py::y"` to the preflight lane -> `test_preflight_measures_the_same_complete_source_inventory_before_r2` red; reverted.
- O5: registered gate NOT run by me (CD44). For the controller.
- O6: in the REPORT.

## For the controller's gate run
- `cd <worktree>/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified`, read markers separately.
- Expect markers: `wheel-installed`, `attestation-hardened`, `self-hosted-lane-passed`, `independent-self-hosting-passed`, `pyflakes-clean` (plus `judge-provenance-bound-to-the-installed-wheel`), `ASSAY_REGISTERED_GATE_COMPLETE=1`.
- Expect none of the 7 retired markers; no `FAILED`.
- The self-hosted lane runs `pytest tests`, which now includes `test_verdict_v13_successors.py`, `test_b106_reuse_and_witness.py` and `test_lane_schema_v2_locked_successors.py` (previously also run as their own phases).

READY-FOR-GATE bb696f53
