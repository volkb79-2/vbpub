I've built the full inventory for all five items without modifying anything. The two most important findings are blockers for the tier plan:

- **`tests/test_liveness.py` leaks into the outer liveness monitor.** Moving the 30–36 s tests to the end of the run without fixing this would mark every mutation candidate as hung.
- **Two heavy files cannot move.** A locked acceptance suite that the release gate runs requires `tests/test_python_qualification.py` and `tests/test_distribution_gate.py` at their current paths.

Evidence throughout is the baseline progress file: 5,831 tests, all passed, 441.7 s of per-test call time against a 519.9 s baseline wall.

---

## 1. Heavy test tier

**Totals.** 113 tests take ≥ 0.5 s each, 317.7 s in all (71.9% of call time). The other 5,718 tests take 124.0 s. Other cut-offs: ≥ 1.0 s is 63 tests / 286.5 s, ≥ 2.0 s is 36 / 248.4 s, ≥ 5.0 s is 17 / 193.7 s.

**Setup cost is not in the evidence.** The durations are the call phase only (`src/assay/liveness.py:207-225`). The plugin does write setup and teardown records, but the progress stream forwards only the call records. So the ~78 s between the 519.9 s wall and the 441.7 s of calls is unaccounted for. That gap includes session and module fixture setup: the `standalone` wheel build, the two release builds in the `built` fixture, and `lint_venv`. The B110 pilot should keep those setup records so fixture costs can be profiled.

**Dominant cost by category:**

| Category | Seconds |
|---|---:|
| (a) waits on a real-time window | 91.0 |
| (d) runs the installed `assay` from PATH | 73.6 |
| (c) nested `assay run` / pytest in-process | 69.9 |
| (b) wheel / venv / pip | 54.2 |
| (e) git history or snapshot work | 24.9 |
| (f) other | 4.2 |

**What each heavy test actually executes** (this matters for items 1 and 2):

| Runs | Seconds | Tests |
|---|---:|---|
| Snapshot `src/assay` in-process | 162.0 | Most files; measured by coverage and can kill mutants |
| Wheel built from the snapshot | 37.7 | `standalone` users, the zipapp tests; can kill mutants, not measured by coverage |
| Unmutated PATH wheel | 73.6 | 7 tests |
| Committed 1.2.5 release wheel | 13.8 | 2 tests |
| No `src/assay` at all | 27.2 | Gate and distribution harness tests |
| Static read of `src` | 2.4 | AST sweeps |
| In-process plus a pytest subprocess | 1.0 | 1 test in `test_liveness.py` |

### Heavy tests by file

Durations are call seconds; line numbers are in the test file.

| File | Heavy / all tests; heavy s / file s | Heavy tests | Category and cost |
|---|---|---|---|
| `test_python_qualification.py` | 13/32; 92.42 / 93.68 | materialize tests :291 (1.88), :315 (1.30), :342 (1.22); :527 (0.57) | (e) git export and commit of the pinned Topos tree (966 entries) |
| | | :563 (13.91), :583 (7.24), :601 (22.01), :621 (8.08), :636 (7.66), :714 (7.19), :752 (7.52) | (d) `installed_assay` fixture (module scope) → run-venv `assay run` |
| | | :771 (3.33), :783 (10.49) | (b) venv + `pip --require-hashes` of the committed 1.2.5 wheel |
| `test_cli_run.py` | 10/52; 74.10 / 77.05 | :463 hang test (30.83) | (a) `_HUNG_CPU_WINDOW_S=30.0` (liveness.py:523), `LIVENESS_IDLE_FLOOR_S=15.0` (:641), `budget_per_candidate="50s"` |
| | | :586 busy loop (35.90) | (a) `"35s"` budget, set deliberately above the 30 s window |
| | | :677 (1.89), :1720 (1.98) | (c) |
| | | six rejudge tests :880/:937/:976/:1017/:1054/:1087 (0.54–0.65 each) | (e) snapshot materialization with a `/bin/sh` judge |
| `test_standalone.py` | 13/20; 34.81 / 36.92 | :216, :248, :832, :908, :1275, :1340, :1423, :1657, :1756 (0.53–3.04) | (c) via `standalone` (session) |
| | | :1064 (5.27) | (a) lane `budget="5s"` vs `sleep 300` |
| | | :1978 (6.01), :2011 (5.45), :2043 (5.32) | (b) each builds its own wheel |
| `test_distribution_gate.py` | 3/26; 17.20 / 20.49 | :370 (8.52), :395 (5.26) | (b) closure venv + wheel of a synthetic repo; `gate_functions` (session) |
| | | :920 (3.42) | (b) `lint_venv` (session) + pyflakes |
| `test_b106_reuse_and_witness.py` | 6/53; 17.11 / 17.13 | :680 (4.73), :787 (1.81), :820 (3.49), :926 (3.45), :1001 (3.12) | (c) `run_lane` R2 with real pytest per mutant |
| | | :550 (0.50) | (c) in-process `assay plan` |
| `test_runner_run_lane_r3.py` | 6/10; 14.15 / 15.07 | :59, :193, :246, :296, :429, :506 (1.98–2.67) | (c) canary halves, real pytest |
| `test_canary_python_pipeline.py` | 8/8; 10.32 | all | (c) |
| `test_mutation_judge_identity.py` | 13/67; 8.96 / 12.15 | :578 ×3 parameters, :817–:1063 (0.52–1.21) | (e/c) two in-process `--resume` runs |
| `test_progress_phase_stream.py` | 2/20; 7.56 | :456 (7.05) | (a) `sleep 7` vs a 5 s heartbeat (`PROGRESS_HEARTBEAT_FLOOR_SECONDS`, runner.py:936) |
| | | :334 (0.50) | (a) `time.sleep(0.35+0.15)` |
| `test_environment_preflight.py` | 3/13; 5.63 | :128 (2.02) | (a) lane `budget="2s"` vs `sleep 45` |
| | | :246 (3.03) | (a) `PROBE_BUDGET_SECONDS` patched 30 → 3 (runner.py:360) |
| | | :343 (0.58) | (e) |
| `test_lane_timeout_writes_a_verdict.py` | 5/16; 5.34 | :120, :152, :303 ×2, :326 (~1.07 each) | (a) `BUDGET="1s"` vs `sleep 30` (file :50-51) |
| `test_distribution_release_wheel.py` | 1/24 | :239 (3.48) | (b) pip refusal; no `src/assay` |
| `test_distribution_build_release.py` | 3/33 | :449, :483, :502 (0.70–1.36) | (b) `built` (module) fixture, whose 2 builds happen in setup |
| Smaller files | | `canary_..._nested_project` 2/2 (2.81); `mutation_classification` 4/13 (2.50, e); `runner_lane_cwd` :270 (2.11, c); `mutation_python_pipeline` 2/2 (2.11, c); `r3_canary_sees_infrastructure` :108 (2.04, c); `..._identity_properties` :94 (1.82, f: hypothesis `max_examples=200`); `gate_qualify_cmru_b006a` :324/:333/:342 (~0.53, c, no `src/assay`); `state_dir_resume` ×2 (e); `untrusted_json_parse_sweep` ×2 (f: re-parses all of `src` per parameter, cacheable); `liveness` :728 (1.03, c); `runner_result_report` :450 (1.00, a: 1 s budget); `mutation_executor_bound`, `mutation_progress_budget_plan`, `mutation_isolation`, `canary_multi_target`, `cli_provenance...` (e); `dependency_purity` :150 and `result_report_wiring_sweep` :143 (f) | |

**Session and module fixtures used by heavy tests:**
- `standalone` (session, conftest.py:1431-1497). The first user alphabetically is `test_analysis.py:721`, which pays for the build. The other users are in `test_dependency_purity`, `test_go_helper_is_packaged` and `test_verdict_schema_is_packaged`.
- `validator` (session, conftest.py:1362).
- `built` (module, test_distribution_build_release.py:362, used by 13 tests).
- `gate_functions` and `lint_venv` (session, test_distribution_gate.py:51 and :690).
- `installed_assay` (module, test_python_qualification.py:550).

### Proposed tier layout

**Directory name.** Use `tests/zz_slow/`, not `tests/slow/`. pytest 8+ collects directories and files together in name order (pytest 9.1.1 checked locally: `_pytest/main.py:569` and `_pytest/pathlib.py:941-943`). So `slow/` would run *before* every `test_*.py` file. The directory must have no `__init__.py`, no `conftest.py` (conftest.py:342-347 explains the `sys.modules['conftest']` collision), and every test file basename must be unique across directories.

**Move whole files** (none of them uses `Path(__file__)`; all import through conftest's `PROJECT_ROOT`):
- `test_standalone.py`
- `test_runner_run_lane_r3.py`
- `test_canary_python_pipeline.py`
- `test_canary_python_pipeline_nested_project.py`
- `test_mutation_python_pipeline.py`
- `test_lane_timeout_writes_a_verdict.py`
- `test_r3_canary_sees_infrastructure.py`
- `test_mutation_judge_identity_properties.py`
- `test_mutation_classification.py` (optional)

**Split mixed files:**

| New file under `tests/zz_slow/` | Tests moved | Helpers to duplicate or move |
|---|---|---|
| `test_cli_run_real_campaigns.py` | cli_run :463, :586, :677, :1720, plus the six rejudge tests | `run` (:127) and `_r2_lane_with_two_candidates` (:765) are shared, so duplicate them; `_seed_cli_rejudge_store` (:842) and `_cli_verify_document` (:873) move |
| `test_b106_witness_real_runs.py` | b106 :680, :787, :820, :926, :1001 | `_seed_pytest_mutation` (:661). **Also** add the new file to `tools/tester-unified-gate.sh:652-654` and extend the check at `test_distribution_gate.py:238-239` |
| `test_distribution_gate_builds.py` | :370, :395, :920 | `lint_venv` moves; duplicate `gate_functions`, `run_bash`, `_make_synthetic_untagged_repo`, `_resolve_ambient_interpreter`, `DISTRIBUTION`, `GATE_SCRIPT` |
| `test_distribution_build_release_artifacts.py` | the `built` fixture plus all 13 of its users | Replace `PROJECT_ROOT = Path(__file__).resolve().parents[1]` (:51) with the conftest import; duplicate the `sys.path.insert` / `import build_release` (:56-57) or load it with `spec_from_file_location` |
| `test_progress_heartbeat_real.py` | :456 | `_R0_LANE`, `_events` |
| `test_environment_preflight_probe_budget.py` | :128, :246 | `_LANE`, `_run` |
| `test_distribution_release_wheel_pip.py` | :239 | constants and `run_helper`, `write_manifest`, `manifest_document` |
| `test_runner_lane_cwd_r3.py` | :270 | `_logging`, `_pwd_log`, `_recorded` |

Optional: the 11 resume tests from `test_mutation_judge_identity.py`, about 7.9 s, which share 8 helpers.

**Projected result.** `zz_slow` holds 133 tests / 223.2 s. The fast tier is 5,666 tests / 124.8 s, and still contains 19 tests between 0.5 and 1.03 s (11.9 s total), which I'd leave in place. Moving `test_standalone.py` does **not** move the `standalone` build; that also needs the 13 other users in the four files listed above.

**Hard path constraints (do not move these files):**
- `tests/test_python_qualification.py` and `tests/test_distribution_gate.py` are required at those paths by the locked P33 carve-asset `nyxloom-trove/carve-assets/P33/test_acceptance_v5.py:406-417`. The release gate runs that suite (`tools/tester-unified-gate.sh:491`) and does not deselect this test (see its deselect list at :493-523).
- `test_b106_reuse_and_witness.py` is named at gate :653.
- `test_lane_schema_v2_locked_successors.py` (:534), `test_verdict_v13_successors.py` (:644) and `test_self_hosting.py` (:367) are also named by path.
- The `test_runner_snapshot_selection.py` node IDs are pinned in `assay.toml:85-86/192-193` and `test_self_lane.py:121-126`.

**Things that need no change:**
- Imports: `from conftest import …` keeps working with pytest's default import mode (precedent: `tests/qualification/test_javascript_real_vitest.py:37`).
- conftest itself: `PROJECT_ROOT` is computed as `Path(__file__).resolve().parent.parent` with an assert (conftest.py:52-56), and `REPO_ROOT = PROJECT_ROOT.parent` (:215). Neither changes because conftest stays put.
- pytest config: `pyproject.toml` sets only `pythonpath=["src"]` and `testpaths=["tests"]`, with no addopts, markers or import mode. The lanes pass `tests`, which recurses into the new directory.
- The pyflakes gate phase (gate :141-142) recurses into the new directory.
- No new pytest hook is needed; B110 rejects non-builtin hooks (backlog :11138).

**Drift tests that pin argv today:**
- `test_self_lane.py:114-115` pins `--override-ini=pythonpath=src`.
- `test_self_lane.py:116-126` pins the deselect set exactly.
- `test_self_lane.py:239-244` requires the preflight argv to equal the qualification argv, apart from the lane name.
- `test_self_hosting.py:79-87` pins the release lane's argv.

For the tier, add a drift test that the tier directory name sorts after every top-level `test_*.py` file and contains no `conftest.py` or `__init__.py`.

**Blocker (from item 5):** fix the `test_liveness.py` leak before reordering.

---

## 2. Tests that only run a pre-built wheel

**A correction to the premise.** `install_locked_release` does **not** build from `source_repo`. It installs the committed `gate/python/release/P25/assay-1.2.5-py3-none-any.whl`, checked by `gate/distribution/release_wheel.py` (qualify_topos.py:36, :58, :668-720).

`installed_assay` (test_python_qualification.py:550-559) returns `shutil.which("assay")`. In this lane that is `run-venv/bin/assay`, a wheel built once from the qualified commit (`tools/self-qualification-gate.sh:92-126, :153-154`). It is never rebuilt per mutant.

| Group | Tests (test_python_qualification.py line) | Call s |
|---|---|---:|
| Unmutated PATH wheel (`installed_assay`) | :563 (13.91), :583 (7.24), :601 (22.01), :621 (8.08), :636 (7.66), :714 (7.19), :752 (7.52) | 73.62 |
| Locked 1.2.5 wheel (`install_locked_release`) | :771 (3.33), :783 (10.49) | 13.83 |
| Neither (harness or git only) | the other 23 tests | 6.24 |

**The whole file never executes snapshot `src/assay`.** It has no `assay` import; the harness itself is asserted import-free at :105-106. Across 3,760 candidates it costs 97.8 worker-hours (PATH wheel 76.9 h, 1.2.5 wheel 14.4 h, rest 6.5 h) and can never kill a mutant. It also contributes nothing to `src/assay` coverage.

**No other file uses the PATH `assay`**, except `test_self_hosting.py:190`, which is already ignored. Some other files never run `src/assay` either, but they are not PATH tests: `test_distribution_release_wheel.py` (asserts it doesn't import assay at :75) and `test_distribution_gate.py` (only builds a wheel to check its version, and runs pyflakes). Excluding those would need its own justification.

**Recommended mechanism:** add `--ignore=tests/test_python_qualification.py` to both self-qualification lanes (`assay.toml:82-90` and `:189-197`). This matches the existing `--ignore=tests/test_self_hosting.py`:
- It is one token, `=`-joined, and already a shape B110 has to handle.
- The file stays at its path, as P33 requires, and the release lane (`assay.toml:44`) keeps running it against the just-built wheel.
- Extend `test_self_lane.py` next to :116-126 so the `--ignore=` set is exactly `{test_self_hosting.py, test_python_qualification.py}`. The :239-244 equality check then covers the preflight lane.

**Alternatives:**
- 9 `--deselect=` tokens: brittle and longer.
- `-m "not path_wheel"`: a new two-token selector, and B110 disallows free-form selectors (backlog :11104-11108); it would also need a registered marker.

Note that today's witness parser already rejects the self-qualification argv because of `--override-ini` (mutation_witness.py:60).

**Policy change needed.** This amends B105's acceptance line "explicitly deselects only the two release-tag audit tests … A drift test pins these exclusions" (backlog :10694-10696) under the "named, justified" rule at :10685. It needs operator or carver sign-off.

---

## 3. Loops that a single mutant can hang

**Inventory.** `src/assay` has 50 `while` loops, 2 `iter(callable, sentinel)` loops and 2 recursive functions. There are no `itertools.count` loops, and no `for` loop grows its own iterable.

**Operator facts** (adapters/python.py):
- compare-swap does not include `in`/`not in` (:447-463).
- falsy-swap only changes `return` values (:620-678).
- `while True` → `while False` makes the loop skip its body, so it crashes instead of hanging.

**The four known go.py sites, confirmed:**

| Site | Mutant | Mechanism |
|---|---|---|
| :292 | `end == -1` → `!=` | Returns 0 for an unterminated raw string, so :356 sets `i = 0` and the scan restarts forever |
| :321 | `two == "//"` → `!=` | At a `\n`, `find` returns `i`, so the cursor stops |
| :323 | `end == -1` → `!=` | A trailing `//` with no newline sets `i = -1` |
| :330 | `close == -1` → `!=` | An unterminated `/*` sets `i = 1`, a step backwards |

**At risk (14 mutants), including the four above:**
- `javascript.py:243, 245, 249`: the same three bugs as go.py.
- `sql_lex.py:193` (×3: `==`, and two `and`→`or`) and `:195`. The committed SQL corpus contains `\n-`, and `tests/test_adapters_sql_lexer.py:57` has a trailing `--` with no newline.
- `go_modfile.py:393`: a trailing `//` sets `index = -1`, and `list(_tokens)` then grows without limit.
- `git.py:335` (×2): `and`→`or` ends in `select()` on an empty selector, which blocks forever when `remaining is None`. `is None`→`is not None` skips draining the pipes, so git output over ~64 KiB deadlocks.
- `liveness.py:1530`: `is not None`→`is None` turns off the timeout, so the busy-loop test at `test_cli_run.py:586` hangs.

**Latent (4 mutants, hang only on input shapes not in the suite):** `sql_lex.py:270, 273`; `isolation.py:1388, 1392`. These grow memory without limit if triggered.

**What a hang costs today.** Scanner spins keep CPU growing, so the liveness monitor never marks them hung. They run to the 1,559.67 s per-candidate budget and land as `budget_exceeded`, about 3× a normal candidate.

**Proposed guard.** Add one helper to `src/assay/errors.py`, which imports only stdlib (:24-28). Putting it there avoids a new `judge.targets` entry, because `test_self_lane.py:128-135` requires the targets to match the files on disk.

```python
def require_advance(old: int, new: int) -> int:
    if new <= old:
        raise AssertionError(f"scanner cursor did not advance ({old} -> {new})")
    return new
```

- Rewrite the cursor assignments at go.py:326, :334, :356; javascript.py:258; sql_lex.py:197, :280; go_modfile.py:393; isolation.py:1406 as `x = require_advance(x, end)`. This adds no branches and no mutation sites.
- Add one direct test in `tests/test_errors.py` for `(3,4)` and for `(3,3)/(3,2)/(3,-1)`. That covers both branches and kills the helper's own `<=`→`<` mutant.
- Rejected alternatives:
  - An inline `assert end > i` at each site adds one mutant per site that correct code can never trip, so each would survive R2.
  - Converting to `for _ in range(n+1)` with an `else: raise` leaves an `else` that correct code never reaches, so each loop gains an uncovered line and branch. (Coverage.py adds no exit arc for a constant `while True`, but this conversion isn't that case.)
- **git.py:335:** rewrite the loop as `while selector.get_map(): … if overflowed: break`. The truthiness test has no mutation sites. This shifts git.py line numbers, and `tests/fixtures/b105-coverage-exclusions.json` pins git.py lines 376-377 and 1342-1343, so update that map in the same commit.
- **liveness.py:1530:** no source change can protect a timeout check from a mutant of itself. Instead, give the real-child tests in `test_cli_run.py` a watchdog: run `main` in a thread, join with a timeout, and kill the child's process group on expiry. The child uses `start_new_session=True` (liveness.py:1375), so today a hung child could be left running as an orphan.

---

## 4. Dataclass flag contract test

**Inventory.** There are 80 `@dataclass(...)` decorators, all at module level, with 148 boolean flags: 80 `frozen=True` and 68 `kw_only=True`. The 12 classes that are frozen but not `kw_only` are `go_modfile.ModuleDeclaration`, python and sql `_Worst`, and the nine config classes. Per module:

| Module | Classes |
|---|---:|
| verdict | 25 |
| config | 9 |
| isolation, mutation, runner | 5 each |
| coverage_parsers/model | 4 |
| base, mutation_parsers/model | 3 each |
| python, sql, git, registry | 2 each |
| 14 other modules | 1 each |

There is also one field-level boolean site, `config.py:1066` (`dataclass_field(default=False, repr=False, compare=False)`), and 12 fields with boolean defaults (for example the adapters' `requires_*_attribution` fields and `verdict.py:2303, 2334, 4375`).

**This is a proven gap.** Six of the first 15 campaign candidates were these flags, and five of the six survived at ~520–545 s each:
- killed: `base.py:77` `frozen`
- survived: `base.py:77` `kw_only`, `:129` `frozen` and `kw_only`, `:164` `frozen` and `kw_only`

Existing tests check frozen-ness for only about 5 classes, by catching `FrozenInstanceError`: `test_diff_added_lines.py:206-216`, `test_coverage_excluded_semantics.py:62-70`, `test_mutation_target.py:31,95`, `test_verdict_mutation_payload.py:69`. Nothing checks this reflectively.

**Spec: `tests/test_dataclass_contract.py` plus `tests/fixtures/dataclass-contract.json`**
1. Walk `PROJECT_ROOT/src/assay/**/*.py`, map each file to a module name, and import it.
2. Collect `vars(module)` classes where `dataclasses.is_dataclass(obj)` and `obj.__module__ == name`.
3. Cross-check with the AST that the set of `@dataclass`-decorated class names in each file equals that set, so a function-local dataclass cannot escape the contract.
4. For each class, record the ten `__dataclass_params__` attributes. For each field, record `init`, `repr`, `compare`, `hash` and `kw_only`, plus `default` when it is a `bool`.
5. Assert `observed == fixture` with set equality on qualified class names, so added or removed classes fail in both directions.
6. To keep the fixture small, store the class parameters plus only the fields that differ from the class defaults. The full per-field dump is about 83 KB.

**Python version check.** `_DataclassParams.__slots__` in 3.14 is `init, repr, eq, order, unsafe_hash, frozen, match_args, kw_only, slots, weakref_slot` (`/usr/local/lib/python3.14/dataclasses.py:346-371`), and 3.13 is the same. `Field.kw_only` exists at :289 and is inherited from the class at :904-905; 3.14 also adds `Field.doc` (:290), which the test should not read. The gate image is Python 3.14 (`tester-unified/Dockerfile:20`). `requires-python` is `>=3.11`, and I have not confirmed whether 3.11's params carry `kw_only`, so rely on per-field `kw_only`, which works everywhere.

I ran a prototype (plain import, no pytest). It found 80 classes, 80 frozen, 68 `kw_only`, 463 fields and 13 boolean defaults, and the AST cross-check passed for every module.

---

## 5. Order-dependence risk

**`src/assay` is clean.** There is no `functools` caching, no module-level container that gets mutated (checked by AST), and registries are `MappingProxyType` or built fresh per call. The only public mutable one is `adjudication.py:225 ADJUDICATORS`, a plain `dict` that nothing mutates. Environment variables are read at call time. There are no class-level mutable attributes and no signal or atexit handlers.

**Real hazard: `tests/test_liveness.py` and the outer liveness monitor.**
- `test_liveness.py:581` and `:601` call the materialized plugin's `pytest_sessionfinish` without redirecting `ASSAY_LIVENESS_EVENTS`. That writes a real `session_finish` record, stamped with the candidate's own pid, into the outer events file.
- From then on the monitor classifies the candidate as HUNG after **30 s** of silence, whether or not CPU is still growing (`liveness.py:1050-1056`, `:1475-1476`, `:1516-1519`, `_HUNG_SESSION_FINISH_GRACE_S=30.0` at :525).
- Today only `test_cli_run.py`'s 30.8 s and 35.9 s tests exceed that, and they run *before* `test_liveness.py`. The longest test after it is 22.01 s. Moving the heavy tier to the end would push the 30–36 s tests past this point, and every candidate would be falsely marked hung.
- The reverse leak also happens. `:656` and `:803` redirect the variable for the whole test body, so the outer run's call records for those two tests land in the tests' temp files. That is confirmed in the evidence: `test_materialized_plugin_writes_valid_json_events` and `..._append_does_not_mutate_or_reuse_identity_fields` have no records, so `tests_completed=5831` is 2 short.
- **Fix:** narrow the redirection to the plugin calls with `with monkeypatch.context() as m: m.setenv(...)`, and assert afterwards. Do not use an autouse `delenv`; that would blind the outer monitor.

**Benign or already controlled:**
- `test_python_qualification.py:701, :741` assign module attributes directly, but `_load_harness` executes a fresh copy each call (:53-59).
- `test_distribution_build_release.py:56` does a permanent `sys.path.insert`. It is harmless, but should use `spec_from_file_location`.
- The `gate_qualify_*` harness loaders register fixed `sys.modules` names at import. That would become a problem only if those files were split.
- Umask and rlimit changes in `test_isolation.py` are restored in `finally`.
- No test writes to `os.environ`, calls `os.chdir` or `signal`, or writes into the source tree.
- The shared fixtures (`schema`, `standalone`, `built`, `lint_venv`) are never mutated.

The prototype and scratch scripts are in `/tmp/claude-1003/-workspaces-vbpub/4d188675-268f-4f79-8aec-e7e00b2aac8a/scratchpad/`; `table.md` there has the full per-test classification table.