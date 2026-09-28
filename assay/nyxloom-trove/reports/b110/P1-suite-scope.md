# B110-P1 — Self-qualification suite scope: exclusions, override removal, a slow tier and the dataclass contract

Revised 2026-09-28 after round-1 review (see REVIEW-2026-09-28-round1.md).

| Field | Value |
|---|---|
| Backlog | **B112** (split from B110) |
| Branch | `assay-b110-p1-suite`, from the integration line `assay-b110-integration` (created by the plan §11.1 reconciliation, C18) **with P0 and P2 merged** |
| Depends on | **P0 and P2 merged** (plan §11.6 order: P2 → P1 → P3c).<br>• P0's `tests/test_liveness.py` leak fix is a hard prerequisite for W2. Without it, moving the 30–36 s tests to the end would make every candidate falsely `hung`.<br>• P2's watchdog already sits on the two real-child liveness tests that W2 moves. |
| Contract class | **2d**: constrained implementation from exact edit maps and prepared proof |
| Implementer | Sonnet (fresh session) |
| Decisions | **A-468** (plan D4, operator): ignore `tests/test_python_qualification.py` in both self-qualification lanes, add a tiered `tests/zz_slow/` layout, drop `--override-ini=pythonpath=src` with a `pyproject.toml` drift pin, and add a reflective dataclass-contract test. A-468 amends B105's acceptance line "deselects only the two release-tag audit tests"; the carver records that amendment in the backlog, not you. |
| Size | M: 2 lane edits, 1 drift-test rewrite, about 9 whole-file moves, 8 file splits, 2 new tests + 1 fixture, docs |

**What this package is for.** It cuts per-candidate work that cannot kill a mutant, and it puts the expensive tests last in the *declared* order. With the P3 cold witness, a kill then stops early. The full declared suite still runs for survivors, and R0/R1 still run it all. Nothing here changes a classification rule, the verdict schema or any production behavior.

**Coordination with P2 and P3c.**
- P2 has already added a watchdog to the two real-child liveness tests `test_run_liveness_classifies_a_thread_join_hang_as_hung` and `test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung`, since P1 starts only after P2 merges.
- W2 moves those two tests out of `tests/test_cli_run.py` **together with P2's watchdog helper and its imports**. Find them **by name**, not by line.
- P3c later edits them in `tests/zz_slow/test_cli_run_real_campaigns.py`.

**Dataclass-contract obligation for later packages** (round-1 P1-3). Once W4 lands, every later package that adds, removes or changes a dataclass in `src/assay` must regenerate `tests/fixtures/dataclass-contract.json` with the command W4 documents, and have the diff reviewed. The carver lists the fixture in those packages' scopes. W5 documents the command in DESIGN-GUIDE.

---

## Context to read first

Paths are relative to `assay/`, verified at HEAD `db85f747`. Re-verify after P0 merges, because P0 adds lines to some of these files.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §0 (host-load rule), §3 D4, §10 (gates).
2. `assay.toml`:
   - `:65-77`: the B105 lane comment;
   - `:78-97`: the `self-qualification` argv (`:82-90`) and `allow_argv_append`;
   - `:184-205`: the `self-qualification-preflight` argv (`:189-197`);
   - `:44`: the release lane `tester-unified`, which keeps `--override-ini=pythonpath=` and still runs `tests/test_python_qualification.py`. Do not touch it.
3. `pyproject.toml`:
   - `:94-100`: `[tool.pytest.ini_options] pythonpath = ["src"]`, `testpaths = ["tests"]`, and the "Developer convenience only" comment, which becomes wrong once B105 relies on the setting;
   - `:36`: a comment naming `tests/test_mutation_judge_identity_properties.py`, which moves.
4. `tests/test_self_lane.py`:
   - `:93-135`: `test_self_qualification_is_full_source_r0_through_r3`. `:114-115` pins the override; `:116-126` pins the deselect set.
   - `:227-257`: `test_preflight_measures_the_same_complete_source_inventory_before_r2`. `:239-244` requires the preflight argv to equal the qualification argv apart from the lane name.
5. Why `test_python_qualification.py` can never kill a mutant:
   - `tests/test_python_qualification.py:546-559` (`installed_assay` is `shutil.which("assay")`, the unmutated run-venv wheel);
   - `:771-790` (the two tests that install the committed 1.2.5 wheel; `:125` names `assay-1.2.5-py3-none-any.whl`, `:778` asserts the release version);
   - `:105-106` (the harness is asserted import-free);
   - `tools/self-qualification-gate.sh:92-126`, `:153-154` (the run-venv wheel is built once from the qualified commit).
6. Pinned test paths that **must not move**:
   - `nyxloom-trove/carve-assets/P33/test_acceptance_v5.py:406-417` (`test_sweep_finds_every_known_consumer` requires `tests/test_python_qualification.py` and `tests/test_distribution_gate.py` to exist and be found by `sweep_v4_consumers.py`);
   - `tools/tester-unified-gate.sh:367` (`test_self_hosting.py`), `:534` (`test_lane_schema_v2_locked_successors.py`), `:644` (`test_verdict_v13_successors.py`), `:652-654` (`test_b106_reuse_and_witness.py`);
   - `tests/test_distribution_gate.py:225-245`, which checks the gate script's marker order via `source.index(...)`. The order assertion is at `:236-242`.
   - The **whole** P33 acceptance file, as the gate runs it: `tools/tester-unified-gate.sh:487-523`, one invocation with an explicit `--deselect` list. Both `test_sweep_finds_every_known_consumer` and `…no_zero_frozen_tree_noise…` read the test tree.
   - `nyxloom-trove/2-product-definition.md`: `status: proven` evidence citations are `tests/<file>.py::<test>` node IDs that nyxloom's `product_evidence.evidence_resolves` resolves. Twelve of them name tests that W2 moves (listed in W2).
7. Collection order:
   - pytest 9.1.1 `_pytest/main.py:569` and `_pytest/pathlib.py:941-943`: directories and files are collected together, sorted by name. So `tests/zz_slow/` comes after every `tests/test_*.py`, and `tests/qualification/` comes before them.
   - `tests/conftest.py:336-354`: why a nested `conftest.py` would collide in `sys.modules['conftest']`, and `collect_ignore_glob`.
   - Precedent for importing `from conftest import ...` from a subdirectory: `tests/qualification/test_javascript_real_vitest.py:37`.
8. The files you split (read each fully before editing):
   - `tests/test_cli_run.py`: helpers `:127` `run`, `:133` `snapshot`, `:765` `_r2_lane_with_two_candidates`, `:805` `_r2_lane_single_site`, `:842` `_seed_cli_rejudge_store`, `:873` `_cli_verify_document`;
   - `tests/test_b106_reuse_and_witness.py:31` (`FIXTURES = Path(__file__).parent / ...`: **must not** be copied verbatim into a subdirectory file) and `:661-1068`;
   - `tests/test_distribution_gate.py:37-110`, `:370-420`, `:690-700`, `:920-940`;
   - `tests/test_distribution_build_release.py:49-60` (`PROJECT_ROOT = Path(__file__).resolve().parents[1]`, `sys.path.insert`, `import build_release`) and `:362-660` (`built` and its 13 users);
   - `tests/test_progress_phase_stream.py:39`, `:79`, `:456`;
   - `tests/test_environment_preflight.py:15`, `:31`, `:128`, `:246`;
   - `tests/test_distribution_release_wheel.py:40-70`, `:239`;
   - `tests/test_runner_lane_cwd.py:42-60`, `:270`.
9. Dataclass contract:
   - `/usr/local/lib/python3.14/dataclasses.py:346-371` (`_DataclassParams.__slots__`) and `:289` (`Field.kw_only`);
   - `src/assay/adapters/base.py:77` (`@dataclass(frozen=True, kw_only=True)`): your controlled break.
10. The per-test cost evidence behind the tier list: `nyxloom-trove/reports/assay-B110-RUNTIME-ANALYSIS-2026-09-28.md`, the appendix with the per-test heavy-test table. It explains the list; the list below is authoritative.

---

## Implementation packet (normative)

### W1. Lane argv (both lanes, identical except for the coverage report path)

The target shape for `[lanes.self-qualification]` is:
```toml
argv = [
  "python", "-m", "pytest", "tests", "-q",
  "--ignore=tests/test_self_hosting.py",
  "--ignore=tests/test_python_qualification.py",
  "--deselect=tests/test_runner_snapshot_selection.py::test_every_release_since_wi1_landed_carries_wi4s_policy_record",
  "--deselect=tests/test_runner_snapshot_selection.py::test_wi1s_own_landing_commit_is_the_state_the_embargo_forbids",
  "--cov=src/assay", "--cov-branch",
  "--cov-report=json:.assay/coverage-self-qualification.json",
]
```

`[lanes.self-qualification-preflight]` gets the same argv with `coverage-self-qualification-preflight.json`.

Token order is fixed: `--ignore` tokens come immediately after `-q`, self_hosting first. No `--override-ini` and no `-o` token may remain.

Why each change is safe:
- **Dropping the override** does not change behavior: `pyproject.toml` already sets `pythonpath = ["src"]` relative to the same rootdir.
- **Ignoring the file** does not change R1: the file never imports `assay`, and the whole-target coverage floor is re-proven by the preflight gate below.

### W1 drift pins (replace `tests/test_self_lane.py:114-126`)

These pin the **exact** argv tuple, so reordering or re-adding a token is caught. They also pin every route by which the override could come back: an argv token, pyproject `addopts`, lane `env` or passthrough `PYTEST_ADDOPTS`, or another inifile (round-1 P1-5).

```python
EXPECTED_QUALIFICATION_ARGV = (
    "python", "-m", "pytest", "tests", "-q",
    "--ignore=tests/test_self_hosting.py",
    "--ignore=tests/test_python_qualification.py",
    "--deselect=tests/test_runner_snapshot_selection.py::test_every_release_since_wi1_landed_carries_wi4s_policy_record",
    "--deselect=tests/test_runner_snapshot_selection.py::test_wi1s_own_landing_commit_is_the_state_the_embargo_forbids",
    "--cov=src/assay", "--cov-branch",
    "--cov-report=json:.assay/coverage-self-qualification.json",
)
assert tuple(lane.argv) == EXPECTED_QUALIFICATION_ARGV
assert not any(
    argument == "-o" or argument.startswith(("-o", "--override-ini"))
    for argument in lane.argv
), "B105 lanes must rely on pyproject.toml's pythonpath, not an override (A-468)"
assert dict(lane.env) == {}
assert "PYTEST_ADDOPTS" not in tuple(lane.env_passthrough)
pytest_ini = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
    "tool"]["pytest"]["ini_options"]
assert set(pytest_ini) == {"pythonpath", "testpaths"}   # no addopts, no other key
assert pytest_ini["pythonpath"] == ["src"]
assert pytest_ini["testpaths"] == ["tests"]
for other in ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"):
    assert not (PROJECT_ROOT / other).exists(), f"{other} would shadow pyproject.toml"
assert not list((PROJECT_ROOT / "tests").rglob("pytest.ini"))
```

`lane.env` and `lane.env_passthrough` are the loaded lane's fields; confirm their exact attribute names in `src/assay/config.py`'s lane dataclass before writing the pin. A different attribute name is not a BLOCKED; use the real one.

In the preflight test (`:227-257`), add the same exact-tuple pin with `coverage-self-qualification-preflight.json`, and `"PYTEST_ADDOPTS" not in lane.env_passthrough`. The existing `:239-244` equality check stays.

### W2. The slow tier

**Directory:** `tests/zz_slow/`. It must have **no** `__init__.py` and **no** `conftest.py`, and every file basename under `tests/` (recursively, excluding `tests/fixtures/`) must be unique.

Moved and split files import shared fixtures and helpers with `from conftest import ...`. They get paths from `conftest.PROJECT_ROOT`, **never** from `Path(__file__)`.

**Whole-file moves.** Use `git mv` and keep the content byte-identical, unless a `Path(__file__)` use forces an edit (none of these has one, verified at `db85f747`):

| From `tests/` | To `tests/zz_slow/` |
|---|---|
| `test_standalone.py` | same name |
| `test_runner_run_lane_r3.py` | same name |
| `test_canary_python_pipeline.py` | same name |
| `test_canary_python_pipeline_nested_project.py` | same name |
| `test_mutation_python_pipeline.py` | same name |
| `test_lane_timeout_writes_a_verdict.py` | same name |
| `test_r3_canary_sees_infrastructure.py` | same name |
| `test_mutation_judge_identity_properties.py` | same name |
| `test_mutation_classification.py` | same name |
| `test_liveness_outer_stream.py` (added by P0) | same name |
| `test_mutation_resource_evidence.py` (added by P0) | same name |
| `test_b105_report_check_real_plan.py` (added by P0; runs a real R2 lane) | same name |

**Splits.** Move the named tests into a new file under `tests/zz_slow/`:
- Move a module-level helper, constant, fixture or import with them if nothing in the source file still uses it; otherwise duplicate it.
- Both files must stay pyflakes-clean (the gate's lint phase covers `tests/` recursively).
- Keep every moved test body byte-identical.

| Source file | Tests to move (by name) | New file `tests/zz_slow/…` | Known helper dependencies |
|---|---|---|---|
| `test_cli_run.py` | `test_run_liveness_classifies_a_thread_join_hang_as_hung`, `test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung`, `test_run_liveness_does_not_turn_a_configure_time_raise_into_a_false_survivor`, `test_run_evaluates_a_real_r3_pass_end_to_end`, and the six `*rejudge*` tests at `:880`, `:937`, `:976`, `:1017`, `:1054`, `:1087` | `test_cli_run_real_campaigns.py` | `run`, `snapshot` (duplicate), `_r2_lane_with_two_candidates`, `_r2_lane_single_site` (move, or duplicate if still used), `_seed_cli_rejudge_store`, `_cli_verify_document` |
| `test_b106_reuse_and_witness.py` | `test_replay_requires_a_current_kill_and_falls_back_to_a_full_run`, `test_witness_capture_works_with_the_existing_liveness_plugin`, `test_custom_sessionfinish_hook_forces_full_suite_fallback`, `test_resume_preserves_witness_prefix_execution_provenance`, `test_rejudge_outcome_disables_prior_witness_replay` | `test_b106_witness_real_runs.py` | `_seed_pytest_mutation` (`:661`). If `FIXTURES` is needed, write it as `PROJECT_ROOT / "tests" / "fixtures" / "verdicts"`. |
| `test_distribution_gate.py` | `test_closure_build_produces_a_real_non_placeholder_dev_identity`, `test_ambient_only_build_without_the_closure_is_refused_as_a_placeholder`, **and the `lint_venv` session fixture (`:691`) together with all six of its users**: `test_a_planted_unused_import_reddens_the_lint_phase` (`:788`), `test_an_undefined_name_reddens_the_lint_phase` (`:819`), `test_a_planted_unused_import_in_a_TEST_module_reddens_the_lint_phase` (`:844`), `test_the_fixtures_tree_is_pruned_and_an_unparseable_fixture_stays_green` (`:871`), `test_a_clone_with_no_tests_tree_refuses_rather_than_linting_nothing` (`:902`), `test_the_shipped_source_tree_is_pyflakes_clean` (`:920`) | `test_distribution_gate_builds.py` | `gate_functions` (duplicate the session fixture; it has other users that stay); `lint_venv` **moves** (no duplicate, so the lint venv is built once per run, round-1 P1-7); `run_bash`, `_make_synthetic_untagged_repo`, `_resolve_ambient_interpreter`, `DISTRIBUTION`, `GATE_SCRIPT` |
| `test_distribution_build_release.py` | the `built` fixture and all 13 tests that take `built` (`:391` … `:656`) | `test_distribution_build_release_artifacts.py` | Replace `PROJECT_ROOT = Path(__file__).resolve().parents[1]` with the conftest import. Load `build_release` by path **and register it in `sys.modules` before executing it**. `gate/distribution/build_release.py:132` has a `@dataclass` under `from __future__ import annotations`, and `dataclasses` resolves `sys.modules[cls.__module__]` while building the class, so an unregistered module crashes at import (round-1 P1-1). Use exactly:<br>`spec = importlib.util.spec_from_file_location("b110_build_release", PROJECT_ROOT / "gate" / "distribution" / "build_release.py")`<br>`build_release = importlib.util.module_from_spec(spec)`<br>`sys.modules[spec.name] = build_release`<br>`spec.loader.exec_module(build_release)` |
| `test_progress_phase_stream.py` | `test_a_real_run_emits_a_real_tick_through_the_real_flag` | `test_progress_heartbeat_real.py` | `_R0_LANE`, `_events` |
| `test_environment_preflight.py` | `test_a_probe_that_exhausts_its_budget_reports_a_timeout_not_a_config_error`, `test_the_probe_cap_is_enforced_where_execute_plan_actually_reads_it` | `test_environment_preflight_probe_budget.py` | `_LANE`, `_run` |
| `test_distribution_release_wheel.py` | `test_pip_require_hashes_rechecks_bytes_mutated_after_a_successful_verify` | `test_distribution_release_wheel_pip.py` | `run_helper`, `write_manifest`, `manifest_document`, and the module constants they read |
| `test_runner_lane_cwd.py` | `test_both_r3_canary_halves_run_in_the_declared_cwd` | `test_runner_lane_cwd_r3.py` | `_logging`, `_pwd_log`, `_recorded` |

**Gate-script and pinned-order coupling for the B106 split:**
- `tools/tester-unified-gate.sh:652-654` runs `test_b106_reuse_and_witness.py` as the v13 suite against the installed wheel. Add `"$worktree/assay/tests/zz_slow/test_b106_witness_real_runs.py" \` as a **second path argument to the same pytest invocation**, on the line directly after the existing path.
- In `tests/test_distribution_gate.py:236-242`, add `v13_real = source.index("zz_slow/test_b106_witness_real_runs.py")` and assert `v13_suite < v13_real < v13`.
- Also assert that the new path is **inside that same command block**, not in a comment (round-1 P1-6):
  1. Take the text from the `-m pytest \` line that precedes `test_b106_reuse_and_witness.py` up to the first following line that does not end in `\`.
  2. Assert `"zz_slow/test_b106_witness_real_runs.py"` occurs in that block.
  3. Assert no line of the block that contains it starts, after `lstrip()`, with `#`.

**`test_distribution_gate.py` split guard** (round-1 P1-8):
- After the split, run the **whole** P33 acceptance file exactly as `tools/tester-unified-gate.sh:487-523` invokes it: the same env (`PYTHONPATH=`, `ASSAY_P26_PROJECT_ROOT`), the same flags and the same `--deselect` list, copied from the script.
- The sweep finds `tests/test_distribution_gate.py` by its *content*, the byte-comparison idiom, which stays in the file.
- If any test of that file fails because of the split, revert **only** this one split, record it as a residual, and continue. Do not edit the sweep or any carve asset.

**Product-definition evidence** (round-1 P1-2, carver decision C15). In `nyxloom-trove/2-product-definition.md`, update exactly these 12 evidence node IDs (verified by grep at `db85f747`; every other citation stays):
- lines 163, 168, 169, 175 and 264: `tests/test_runner_run_lane_r3.py::…` becomes `tests/zz_slow/test_runner_run_lane_r3.py::…`;
- lines 437, 438, 439, 444, 451, 452 and 458: `tests/test_distribution_build_release.py::…` becomes `tests/zz_slow/test_distribution_build_release_artifacts.py::…`. These are `test_the_zipapp_reports_the_wheels_version_and_never_the_source_fallback`, `test_the_zipapp_reads_its_packaged_schema_from_inside_the_archive`, `test_the_zipapp_verifies_a_real_artifact_and_refuses_a_foreign_version`, `test_the_zipapp_propagates_a_nonzero_exit_from_a_failing_lane`, `test_two_builds_of_one_commit_are_byte_identical`, `test_the_archive_carries_no_builder_specific_paths` and `test_an_untagged_build_emits_no_release_manifest`.

Change only the path prefix; keep the test names. Re-run the grep after W2 to confirm nothing else names a moved test:
`python3 -c` over `re.finditer(r"tests/[A-Za-z0-9_/]+\.py::[A-Za-z0-9_]+", text)`, checking each cited file exists and defines that test name.

**Do not move:**
- `test_python_qualification.py`, `test_distribution_gate.py` or `test_distribution_build_release.py` themselves (they are only split);
- `test_self_hosting.py`, `test_lane_schema_v2_locked_successors.py`, `test_verdict_v13_successors.py`, `test_b106_reuse_and_witness.py`;
- `test_runner_snapshot_selection.py`, whose node IDs are pinned in `assay.toml` and `test_self_lane.py`.

Do not edit historical manifests that mention old paths (`carve-assets/P33/migration-manifest.json`, `carve-assets/W1/migrate_v5_to_v6.py`, `carve-assets/W4/MANIFEST.md`).

**Comments that name a moved file** (`src/assay/provenance.py`, `src/assay/adapters/go.py`, `src/assay/cli.py`, and `pyproject.toml:36`): update the path text in the comment only.
- Find them with `grep -rn -e tests/test_standalone.py -e tests/test_cli_run.py -e tests/test_distribution_build_release.py -e tests/test_mutation_judge_identity_properties.py src/assay pyproject.toml`.
- Also search for the other moved basenames.
- These are comment-only edits: no code, and no line additions or removals.
- Historical records in `nyxloom-trove/` (handoffs, carves, STATE, backlog, decisions) are **not** edited. Only `2-product-definition.md`'s machine-resolved evidence is (above).

### W3. Tier drift test (new, `tests/test_suite_layout.py`, fast tier)

```python
def test_slow_tier_is_collected_after_every_top_level_test_module():
    tests_dir = PROJECT_ROOT / "tests"
    tier = tests_dir / "zz_slow"
    assert tier.is_dir()
    assert not (tier / "__init__.py").exists() and not (tier / "conftest.py").exists()
    assert int(pytest.__version__.split(".")[0]) >= 8  # name-sorted dir/file interleaving
    siblings = [p.name for p in tests_dir.iterdir() if p.name != "__pycache__"]
    assert max(siblings) == "zz_slow", "pytest collects tests/ entries in name order; zz_slow must be last"
    assert list(tier.glob("test_*.py")), "an empty slow tier is a layout regression"

def test_every_test_module_basename_is_unique():
    names = [p.name for p in (PROJECT_ROOT / "tests").rglob("test_*.py")
             if "fixtures" not in p.relative_to(PROJECT_ROOT / "tests").parts]
    assert len(names) == len(set(names))

# Pins the move list (round-1 P1-6): an implementation that moves only one file,
# or leaves a split test behind, fails here rather than silently shrinking the tier.
EXPECTED_SLOW_FILES = frozenset({
    # whole-file moves
    "test_standalone.py", "test_runner_run_lane_r3.py", "test_canary_python_pipeline.py",
    "test_canary_python_pipeline_nested_project.py", "test_mutation_python_pipeline.py",
    "test_lane_timeout_writes_a_verdict.py", "test_r3_canary_sees_infrastructure.py",
    "test_mutation_judge_identity_properties.py", "test_mutation_classification.py",
    "test_liveness_outer_stream.py", "test_mutation_resource_evidence.py",
    "test_b105_report_check_real_plan.py",
    # split targets
    "test_cli_run_real_campaigns.py", "test_b106_witness_real_runs.py",
    "test_distribution_gate_builds.py", "test_distribution_build_release_artifacts.py",
    "test_progress_heartbeat_real.py", "test_environment_preflight_probe_budget.py",
    "test_distribution_release_wheel_pip.py", "test_runner_lane_cwd_r3.py",
})
EXPECTED_SPLIT_TESTS = {  # split target -> exact set of module-level test function names
    "test_cli_run_real_campaigns.py": {...},        # the 10 names from the W2 table
    "test_b106_witness_real_runs.py": {...},        # the 5 names
    "test_distribution_gate_builds.py": {...},      # the 8 names (2 build tests + 6 lint_venv users)
    "test_distribution_build_release_artifacts.py": {...},  # the 13 `built` users
    "test_progress_heartbeat_real.py": {...},
    "test_environment_preflight_probe_budget.py": {...},
    "test_distribution_release_wheel_pip.py": {...},
    "test_runner_lane_cwd_r3.py": {...},
}

def test_slow_tier_holds_exactly_the_reviewed_files_and_split_tests():
    tier = PROJECT_ROOT / "tests" / "zz_slow"
    assert {p.name for p in tier.glob("test_*.py")} == EXPECTED_SLOW_FILES
    for name, expected in EXPECTED_SPLIT_TESTS.items():
        tree = ast.parse((tier / name).read_text(encoding="utf-8"))
        got = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
        assert got == expected, name
```

Fill every `{...}` with the literal names from the W2 split table (and the six `*rejudge*` names you find at the listed lines). The literals are the reviewed move list; do not compute them.

### W4. Dataclass contract (new, `tests/test_dataclass_contract.py` + `tests/fixtures/dataclass-contract.json`)

```python
PARAMS = ("init", "repr", "eq", "order", "unsafe_hash", "frozen",
          "match_args", "kw_only", "slots", "weakref_slot")
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "dataclass-contract.json"

def _available_params(cls) -> tuple[str, ...]:
    """Feature-detect instead of guessing versions (round-1 P1-7): the params this
    interpreter's _DataclassParams actually carries, in PARAMS order."""
    slots = set(getattr(type(cls.__dataclass_params__), "__slots__", ()))
    return tuple(p for p in PARAMS if p in slots)

def _module_name(path: Path) -> str: ...        # src/assay/x/y.py -> "assay.x.y"; __init__ -> package
def _is_dataclass_decorator(node: ast.expr) -> bool: ...
    # Name "dataclass", Attribute ".dataclass", or a Call whose func is either
def _declared(path: Path) -> tuple[set[str], list[str]]: ...
    # (module-level ClassDef names with a dataclass decorator,
    #  "path:lineno Name" for any dataclass-decorated ClassDef NOT directly in module.body)
def _observe() -> dict: ...
def test_every_dataclass_matches_its_reviewed_contract(): ...
if __name__ == "__main__":         # fixture (re)generation; a contract change is reviewed
    print(json.dumps(_observe(), indent=2, sort_keys=True))
```

`_observe()` does the following. For each `path` in `sorted((PROJECT_ROOT / "src" / "assay").rglob("*.py"))`:
1. `module = importlib.import_module(_module_name(path))`.
2. `reflected` is the set of `obj.__qualname__` for the `obj` in `vars(module).values()` where `isinstance(obj, type) and dataclasses.is_dataclass(obj) and obj.__module__ == module.__name__`.
3. Let `declared, nested = _declared(path)`. **Assert** `nested == []` and `reflected == declared`, naming the path in the message.
4. For each class, the params are `{p: getattr(cls.__dataclass_params__, p) for p in _available_params(cls)}`. The field deviations are computed for each `f` in `dataclasses.fields(cls)`:
   - `flags` is `{"init": f.init, "repr": f.repr, "compare": f.compare, "hash": f.hash, "kw_only": f.kw_only}`;
   - `defaults` is `{"init": True, "repr": True, "compare": True, "hash": None, "kw_only": <class kw_only>}`.
     - The class `kw_only` is `cls.__dataclass_params__.kw_only` when `"kw_only" in _available_params(cls)`.
     - Otherwise it is `all(f.kw_only for f in fields)` when the class has fields, else `False`. A zero-field `kw_only=True` class on such an interpreter records `kw_only` only via params, never via fields.
   - the deviation is `{k: v for k, v in flags.items() if v != defaults[k]}`, plus `"default": f.default` when `isinstance(f.default, bool)`;
   - a field appears in the fixture only if its deviation is non-empty.
5. The result is `{"format": 1, "classes": {"<module>.<qualname>": {"params": {...}, "fields": {...}}}}`.

The fixture is generated on the gate image (Python 3.14, all ten params). When an interpreter exposes fewer params, the test compares the fixture's params **restricted to `_available_params`**, and the field entries unchanged. Python 3.13 carries the ten slots; 3.12 could not be verified in the carve, and feature detection makes that unnecessary.

`test_every_dataclass_matches_its_reviewed_contract` asserts `observed == expected`. On a mismatch, its message lists the classes added, the classes removed and, per changed class, the differing keys.

**Generate the fixture once** with `cd assay && PYTHONPATH=src:tests python tests/test_dataclass_contract.py > tests/fixtures/dataclass-contract.json`. Commit it in the **same commit** as the test. The reviewer diffs it against the source decorators.

This exact command is the **regeneration command** that every later package uses. W5 documents it in DESIGN-GUIDE.

### Decision table

| Change | Effect on R0/R1 | Effect on R2 | Legal? |
|---|---|---|---|
| `--ignore` of `test_python_qualification.py` | 32 tests leave the B105 lanes. The release lane still runs them. R1 is unaffected: the file never imports `assay`, and the preflight gate re-proves it. | Those tests could never kill a mutant; they no longer cost per-candidate time. | Yes (A-468) |
| Drop `--override-ini=pythonpath=src` | none (pyproject is identical) | Removes the witness-eligibility blocker at `mutation_witness.py:60-63` | Yes (A-468) |
| Move or split tests into `zz_slow` | same tests, same assertions, different order | The expensive tests come last in the declared order | Yes, only if every test's pass/fail is order-independent. P0's leak fix is the known prerequisite; any new order failure is a true red to fix, never to skip. |
| Dataclass contract test | +1 test | Kills flag-flip mutants in milliseconds | Yes |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| W1 argv + pins | `assay.toml`, `tests/test_self_lane.py` | O1 | the real lane file | re-add `--override-ini=pythonpath=src` to one lane → O1 fails; drop one `--ignore` → O1 fails; swap the two `--ignore` tokens → the exact-tuple pin fails |
| W1 pyproject pin | `pyproject.toml` (comment only), `tests/test_self_lane.py` | O2 | the real `pyproject.toml` | change `pythonpath` to `["src", "tests"]` → O2 fails; add `addopts = "-o pythonpath=x"` → the key-set pin fails; create `tests/pytest.ini` → O2 fails |
| W2 moves/splits | `tests/**` | O3, O4 | the real suite | leave a moved test's helper behind → pyflakes/collection error → gate fails; revert P0's leak fix → preflight shows `hung`/errors |
| W2 evidence IDs | `nyxloom-trove/2-product-definition.md` | O8 | the 12 citations | leave one old path → O8's resolution check fails |
| W2 gate coupling | `tools/tester-unified-gate.sh`, `tests/test_distribution_gate.py` | O5 | the real script | swap order of the two paths → O5 fails; put the path only in a `#` comment → the block check fails |
| W3 layout | `tests/test_suite_layout.py` | O6 | the real tree | add `tests/zz_slow/conftest.py` or a `tests/zzz_*.py` file → O6 fails; leave one split test behind → the move-list pin fails |
| W4 contract | `tests/test_dataclass_contract.py` | O7 | `tests/fixtures/dataclass-contract.json` | set `kw_only=False` at `src/assay/adapters/base.py:77` → O7 fails (run locally, do not commit); flip a **field-level** flag, e.g. change `repr=False` to `repr=True` in the `dataclass_field(default=False, repr=False, compare=False)` at `src/assay/config.py:1066` → O7 fails; add a function-local `@dataclass` in a scratch module → O7 fails |

### Degrees of freedom

You choose the helper duplication details (move vs. duplicate), the private helper names inside the new test files, and the exact assertion-message wording. You do not choose: the list of tests, the tier directory name, the argv token order, the fixture shape, or which files must not move.

---

## Work

1. **W1.**
   - Edit both lane argvs in `assay.toml` to the target shape.
   - Update the B105 lane comment at `assay.toml:65-77` to say `test_python_qualification.py` is excluded because it exercises only the prebuilt run-venv wheel and the committed 1.2.5 release wheel, never the snapshot's `src/assay`, so it can neither measure coverage nor kill a mutant (A-468); the release lane keeps running it.
   - Rewrite the `pyproject.toml:96-97` comment: `pythonpath = ["src"]` is now load-bearing for the B105 lanes, pinned by `tests/test_self_lane.py`, and is still not a ship signal.
   - Apply the W1 drift pins.
   - Run the focused tests.
   - Commit.
2. **W3 first, then W2.** Add `tests/test_suite_layout.py`; it is red until `zz_slow` exists.
   - Then do the moves (`git mv`), the splits, the gate-script line and the `test_distribution_gate.py` order and block pins.
   - Update the comment paths, including `pyproject.toml:36`, and the 12 `2-product-definition.md` evidence IDs.
   - Run the focused tests, then the whole suite once, serially, with the lane's own selection: `nice -n 19 ionice -c3 python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_self_hosting.py --ignore=tests/test_python_qualification.py`.
   - Run the whole-file P33 guard (Gate step 1).
   - Commit.
3. **W4.**
   - Write the test.
   - Generate the fixture.
   - Inspect it: it must list exactly one entry per module-level `@dataclass` in `src/assay`.
   - Run the controlled breaks locally, then revert them.
   - Commit.
4. **Docs** (below). Commit.

## Oracles

Each oracle names its **observable** and its **negative**. Its gate is the focused file, then `tester-unified`, then `self-qualification-preflight`.

- **O1. Lane argv pins.**
  - Observable: both lanes have the exact `--ignore` set, no override token, the unchanged deselect set, and preflight/qualification argv equality.
  - Negative: an implementation that edits only one lane fails `:239-244`. One that keeps the override fails the new pin.
- **O2. The pyproject pin.**
  - Observable: `pythonpath == ["src"]` and `testpaths == ["tests"]`.
  - Negative: a later edit of pyproject silently changes what B105 snapshots import.
- **O3. The same suite, reordered.**
  - Observable: the collected node set of `pytest --collect-only -q tests --ignore=tests/test_self_hosting.py --ignore=tests/test_python_qualification.py` equals the set before the move, minus nothing, apart from the new W3/W4 tests and the path prefix change of moved nodes. Compare by `(basename, test name)` pairs, recorded before and after in your report. Every `zz_slow` node comes after every non-`zz_slow` node in that output.
  - Negative: a split that drops or duplicates a test.
- **O4. Order independence holds.**
  - Observable: the lane-selection full run above is green, and the `self-qualification-preflight` gate is green, with R1 still 100% line and branch.
  - Negative: a moved test that depended on an earlier test's leftovers fails here. Fix that test's own setup. Never reorder to hide it.
- **O5. The installed-wheel v13 suite still runs the moved B106 tests.**
  - Observable: the new `source.index` order assertion; the tester-unified gate log shows the `verdict-v13-successors-verified` phase marker.
  - Negative: forgetting the gate path leaves 5 B106 tests out of the installed-wheel check.
- **O5 (addendum).** The zz_slow B106 path appears inside the same `-m pytest` command block as `test_b106_reuse_and_witness.py`, on a non-comment line.
  - Negative: a path that exists only in a comment satisfies `source.index` but fails the block check.
- **O6. Layout.** Observable: the W3 tests, including the exact move-list pin. Negative: see the controlled breaks. Moving only one file, or leaving a split test in its source file, fails the move-list pin.
- **O7. Dataclass contract.**
  - Observable: green on the real tree. All three controlled breaks go red with a message naming the class: the class-level `kw_only` flip, the field-level `repr` flip, and the nested dataclass.
  - Negative: a test that only counts classes passes the `kw_only` flip; one that diffs params only passes the field-level flip. This test catches both.
- **O8. Product-definition evidence still resolves.**
  - Observable: after W2, every `tests/…py::name` citation in `nyxloom-trove/2-product-definition.md` names an existing file that defines that test function. Check with the grep script in W2; record its output in the report.
  - Negative: an implementation that moves the tests but forgets the 12 citations leaves them unresolved, and nyxloom's `evidence_resolves` would fail them.

### What an oracle must NOT contain (AUTHORING.md §3b, verbatim)

### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

Every rule below is the residue of a real incident; the `L`/`PL` refs are the
write-ups in `reference/LESSONS.md`. **If a handoff asks an agent to write
tests, copy this list into it** — an implementation agent has no access to our
incident history and will otherwise reproduce these by default.

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**F. No predicted measurements.** (distilled 2026-09-17 from an incident in a
consuming project's own decision ledger — the specific entry isn't cited here
since a canonical doc shouldn't hard-reference a consumer's private,
renumberable ledger; see that project's own decisions.md around the same
date for the full incident writeup if useful.)
- ✗ A carve or oracle asserting a specific coverage/mutation number, a "missing
  lines" list, or a "this branch is permanently uncoverable" claim computed by
  reasoning about a tool's rendered report instead of running the tool.
- ✗ Trusting `coverage.py`'s rendered "Missing" column as a complete branch-arc
  list — it silently suppresses an arc whose destination line is already
  reported missing elsewhere, so a hand-derived read of the report undercounts
  by exactly that arc. This exact mistake recurred three times independently
  in one wave before being traced to this display artifact.
- ✓ Assert the POLICY requirement instead — the project's coverage target, its
  R0-R3 (or equivalent) testing tier, the design decision — as the oracle.
  Never a predicted number; the number does not exist until the implementer's
  own gate run produces it.
- ✓ If a carve must justify "this is achievable" or "this line is
  unreachable" before dispatch, PROVE it by executing the tool
  (`coverage.py`/`runpy.run_module(mod, run_name="__main__")`, or the
  project's own judge) against real or synthetic stand-in code — never by
  reading a report and reasoning about what it would show.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

---

## Docs sync

Make these changes in the same branch.
- **`docs/CONSUMERS.md:59-77`** (B105 section):
  - replace "the two release-tag audit tests are explicitly deselected here" with the full statement: two release-tag audit tests are deselected, and `tests/test_self_hosting.py` and `tests/test_python_qualification.py` are ignored, each with its one-line reason;
  - state that the ordinary release lane still runs all of them;
  - add one sentence on the `tests/zz_slow/` tier: the same tests, collected last, so the expensive ones come last in the declared order.
- **`docs/DESIGN-GUIDE.md:1934-1956`** (§"Full-source self-qualification (B105)"): the same facts, with the *why*:
  - wheel-only tests cannot observe a mutant;
  - declared order is free to choose, but reordering per candidate is not (A-464);
  - `pythonpath` comes from `pyproject.toml`.

  Cite A-468.
- **`docs/DESIGN-GUIDE.md`, new short subsection "Dataclass contract fixture"** (next to the B105 section). State:
  - that `tests/test_dataclass_contract.py` pins every `src/assay` dataclass's parameters and non-default field flags against `tests/fixtures/dataclass-contract.json`;
  - why: flag-flip mutants such as `frozen`/`kw_only` otherwise survive R2;
  - the exact regeneration command `cd assay && PYTHONPATH=src:tests python tests/test_dataclass_contract.py > tests/fixtures/dataclass-contract.json`;
  - that any change to that fixture is a reviewed contract change in the same commit as the source change.
- **`README.md:955-975`** (B105 bullet): one sentence on the excluded wheel-only module.
- **`CHANGES.md` `## [Unreleased]`:**
  - `### Changed`: B105 lane scope and the slow tier;
  - `### Testing`: the dataclass contract and the layout tests.

Keep `tests/test_docs_examples_and_vocabulary.py` green.

## Scope / forbid

**Touch only:**
- `assay.toml`: the two B105 lanes' `argv` and their comment block;
- `pyproject.toml`: the comment at `:94-95` and the path in the comment at `:36` only;
- `tests/**`: the moves, splits and new files above, `tests/test_self_lane.py`, `tests/test_distribution_gate.py` (order and block pins only), and `tests/fixtures/dataclass-contract.json`;
- `tools/tester-unified-gate.sh`: one added path line;
- comment lines in `src/assay/{provenance,cli}.py` and `src/assay/adapters/go.py`;
- `nyxloom-trove/2-product-definition.md`: exactly the 12 evidence node-ID path prefixes listed in W2, nothing else;
- `README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `CHANGES.md`.

**Forbidden:**
- any non-comment `src/assay` change;
- the release lane `tester-unified`, `run-gate.toml`, `tools/self-qualification-gate.sh`;
- any carve asset or historical manifest;
- `tests/fixtures/b105-coverage-exclusions.json`;
- skipping, `xfail`ing or deleting a test;
- `nyxloom-trove/4-backlog.md` and `decisions.md`.

Anything else is a BLOCKED trigger.

## Gate

**Host-load rule (plan §0, verbatim):**

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. **Focused tests, serially:**
   `cd <worktree>/assay && nice -n 19 ionice -c3 python -m pytest tests/test_self_lane.py tests/test_suite_layout.py tests/test_dataclass_contract.py tests/test_distribution_gate.py tests/zz_slow/test_distribution_gate_builds.py tests/zz_slow/test_distribution_build_release_artifacts.py -q -p no:cacheprovider`.
   Then run the **whole** P33 acceptance file with exactly the invocation of `tools/tester-unified-gate.sh:487-523`: the same `PYTHONPATH=`, `ASSAY_P26_PROJECT_ROOT`, flags and `--deselect` list, copied from the script, but with the devcontainer `python` under `nice -n 19 ionice -c3`. Then run the O8 citation check.
2. **Registered gate:**
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p1-tester-unified.log 2>&1; echo "exit=$?"`.
   Then, as a separate step:
   `grep -E 'ASSAY_GATE_CONTAINER_EXIT=|ASSAY_REGISTERED_GATE_COMPLETE=|verdict-v13-successors-verified' /tmp/b110-p1-tester-unified.log`.
3. **B105 preflight** (required: lane argv and collected tests changed):
   `cd <worktree>/assay && python ./run-gate.py self-qualification-preflight > /tmp/b110-p1-preflight.log 2>&1; echo "exit=$?"`.
   Read `B105_VERIFIED_LANE=self-qualification-preflight` in a separate step. Record the preflight's baseline wall time (its `command_finished`) as **information only**, never as an oracle.

**Never run the `self-qualification` lane.**

*Non-oracle expectation*, for the report only: the analysis projected a fast tier of about 5,666 tests / 125 s of call time and a slow tier of about 133 tests / 223 s. Report what you observe; no test may assert it.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P1-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

Examples of BLOCKED triggers:
- a moved test fails for an order reason whose fix needs a forbidden file;
- the whole-file P33 guard fails even after reverting the `test_distribution_gate.py` split;
- a `2-product-definition.md` citation names a moved test that is not among the 12 listed; do not widen the edit set yourself.

A product gap is a `D-<NNN>` entry in the report, not a BLOCKED. For example: you believe another wheel-only module should also be ignored. Do not act on it.

## Report

Write `nyxloom-trove/reports/assay-B110-P1-REPORT.md` and return:
- the head commit and per-item commits, each with trailer `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`;
- `git diff --stat`;
- the traceability table, with actual node IDs and controlled-break failure counts;
- the O3 before/after node-set comparison summary (counts plus any difference);
- the gate log paths, with markers read separately;
- the preflight baseline wall time (information);
- residuals, including any reverted split.

**Checkpointing:** ARM at ~120k context or ~60 tool calls. CUT at a green, committed work-item boundary, with a continuation brief and retention prompt in the report.
