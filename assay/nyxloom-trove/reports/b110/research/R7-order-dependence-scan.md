Item 5 only (order-dependence risk): I scanned `src/assay` module state and the tests' global-state mutation, read-only. I found one real problem, confirmed in the evidence file: `tests/test_liveness.py` writes into the outer run's liveness events file, in both directions. Everything else is benign or only matters if files get split.

## 5. Order-dependence risk

### 5A. Module-level mutable state in `src/assay`: none that creates order-dependence

| Check | Result | Evidence |
|---|---|---|
| `functools.lru_cache` / `cache` / `cached_property` | None. `functools` is not imported anywhere in `src/assay`. | grep: 0 hits |
| Module-level dict/list/set mutated at runtime | None. An AST scan found every module-level container, then looked for `.append/.update/[k]=/del/.add/.pop/global` on it: 0 hits. | See the next row for the containers. |
| Module-level containers that are read-only lookup tables | `go_stmtpos.py:100 _FORCED_ENV` (copied into a fresh env at :244, never mutated); `javascript.py:291-292 _OPENERS/_CLOSERS`; `python.py:454/468/474 _COMPARE_SWAP/_BOOLOP_SWAP/_EMPTY_COLLECTION_TEXT`; `analysis.py:361 _REPORT_EXITS`; `isolation.py:1484 _DECLARED_KIND_NAMES`; `mutation.py:3087 _DISCARD_REASON_BY_STATUS`; `adjudication.py:225 ADJUDICATORS` | `ADJUDICATORS` is the only public mutable one: a plain `dict` typed as `Mapping`. Tests only read it (`test_adjudication_registry.py:63`). Optional hardening: wrap it in `MappingProxyType`. |
| Registries | All immutable. `coverage.py:105 FORMAT_REGISTRY`, `mutation_parsers/__init__.py:85`, `result_reports/__init__.py:62` are `MappingProxyType` (plus a `frozenset` at :71). `registry.py:164 new_registry()` builds a fresh proxy per call; there is no global registry. `adapters/__init__.py` does no discovery. | — |
| Module-level spec objects | Frozen: `isolation.py:150 DEFAULT_SNAPSHOT_LIMITS` (`SnapshotLimits` is frozen at :113), `coverage.py:88 FormatSpec` is frozen, `MutationFormatSpec` is a NamedTuple (`mutation_parsers/__init__.py:71`). | — |
| Class-level mutable attributes | None (AST scan: 0). Locks are per instance: `isolation.py:586`, `mutation.py:907`. | — |
| `global` statements | Only inside plugin source strings: `mutation_witness.py:423,465` (inside `_PLUGIN_SOURCE` from :324) and `liveness.py:230` (inside `_PLUGIN_SOURCE` from :151). They only run in materialized plugin copies. | — |
| `os.environ` reads | All happen at call time, none at import: `git.py:406`, `runner.py:806`, `cli.py:1180`, `mutation_witness.py:64`. The `os.environ` aliases there are read-only. | — |
| Values bound at import time | `__init__.py` sets `__version__ = version("assay")`. `cli.py:72` and `analysis.py:20` bind `from . import __version__` when they are first imported; `mutation.py:1047` reads it lazily. This only interacts with the `importlib.reload` tests in 5B, and it is safe there. | — |
| `sys.modules` | Read only (`provenance.py:216`). | — |
| Signal handlers, `atexit`, logging config, `setrecursionlimit` | None in `src/assay`. The atexit mention is a comment in `liveness.py:32`. | — |

### 5B. Tests that mutate global state

#### Real risk: the two-way leak between `test_liveness.py` and the outer liveness plugin

This matters whenever assay's own suite runs under assay liveness, which is the B105 self-qualification lane (`liveness = true`, `assay.toml:177`). The materialized plugin's `_append` reads `ASSAY_LIVENESS_EVENTS` at every call and stamps `os.getpid()` (`liveness.py:166-193`).

**(a) Fake `session_finish` records go into the outer events file.**
- `test_liveness.py:570-587` calls `plugin.pytest_sessionfinish(session=None, exitstatus=3)` at :581.
- `test_liveness.py:590-606` calls it with `exitstatus=0` at :601.
- Neither test isolates `ASSAY_LIVENESS_EVENTS`, so both write `{"event":"session_finish",...,"pid":<outer pytest pid>}` into the real run's events file.
- The monitor only accepts a finish whose pid matches the candidate pid (`liveness.py:1050-1056`). This record passes that check because it was written by the candidate pytest process itself.
- Once it is seen, `session_finish_at` stays set (`liveness.py:1475-1476`). From then on a candidate is classified HUNG after 30 s without events, whether or not CPU is still growing (`liveness.py:1508-1512`; `_HUNG_SESSION_FINISH_GRACE_S = 30.0` at :525).
- Without the fake record, the bound would be the calibrated `expect_next_event_within_s = 107.7` s *and* no CPU growth (from the `plan` event in the evidence file).
- Tests that run after `test_liveness.py` alphabetically come close to 30 s with no event: `test_python_qualification.py::test_integrity_matrix_negatives_produce_their_frozen_terminals` takes 22.01 s, `test_run_scenario_reproduces_the_locked_missing_line_terminal` 13.91 s. On a loaded host these can cross 30 s and produce a false `LivenessHungExpired`.
- This is order-dependent: any heavy test placed after `test_liveness.py` in the same process increases exposure. `test_cli_run.py` (30.8 s and 35.9 s liveness tests) would trip it for certain if it ran after.

**(b) Outer `test` records go into the tests' temp files.**
- `test_liveness.py:656` and `:803` `monkeypatch.setenv(ASSAY_LIVENESS_EVENTS_ENV, <tmp>)` for the whole test body.
- The outer plugin's call-phase report is written before monkeypatch undoes the change at teardown, so it lands in the test's temp file.
- **Confirmed in the evidence file:** `test_materialized_plugin_writes_valid_json_events` and `test_materialized_plugin_append_does_not_mutate_or_reuse_identity_fields` are the only tests with no `test` record that were not skipped or deselected.
  - I compared the evidence nodeids against the AST-defined tests. The other 14 missing ones are expected: 11 in `test_gate_qualify_dstdns_sql` (skipped by the docker fixture), the 2 deselected tag tests, and 1 skipif in `test_standalone.py:154`.
  - So `tests_completed = 5831` undercounts by at least 2.

**Fix for both:** keep the redirection tight around the hook calls, and do the assertions after the scope closes:
```python
with monkeypatch.context() as m:
    m.setenv(liveness.ASSAY_LIVENESS_EVENTS_ENV, str(tmp_path / "events.ndjson"))
    ...hook calls...
# assertions here
```
Do this in the four tests above, or route it through `_load_materialized_plugin` (`test_liveness.py:532-545`) with a context-manager variant. **Do not use an autouse `delenv`**: the real outer plugin reads the same variable at every hook, so that would stop all outer events and cause false hangs by itself.

#### Benign or already controlled

| Site | What it does | Why it's safe (or when it wouldn't be) |
|---|---|---|
| `test_b105_source_coverage_controls.py:38-53` (and `test_self_hosting.py:232-239`, which is `--ignore`d) | `importlib.reload(assay)` with a patched `importlib.metadata.version` | Reloaded again in `finally`. The reload only rebinds `__version__`: submodules are already in `sys.modules`, so `from .config import …` rebinds the same objects. `cli`/`analysis` are not first-imported inside that window. |
| `test_python_qualification.py:53-59, :701, :741` | Direct `module.materialize_scenario = …` and `module._seed_baseline = …` | `_load_harness()` runs `module_from_spec` and `exec_module` on every call and overwrites `sys.modules['p25_qualify_topos_ordinary']`, so each patch only touches a fresh private copy. Nothing imports it by name. |
| `test_gate_qualify_dstdns_sql.py:51-56`, `test_gate_qualify_cmru_b006a.py:39-44` | Load the harness once at import and register it in `sys.modules` under a fixed name | Tests patch it only via `monkeypatch` (no direct `q.X =` found). **Latent issue if a file is split:** two files each running this loader would replace each other's `sys.modules` entry and split class identity. Use one shared loader that reuses the existing `sys.modules` entry. |
| `test_distribution_build_release.py:56` | Permanent `sys.path.insert(0, gate/distribution)` at import, never undone | That directory only holds `build_release.py` and `release_wheel.py`, which collide with nothing. Hygiene fix: load it with `spec_from_file_location`. |
| `test_gate_qualify_dstdns_sql.py:950`, `test_gate_qualify_cmru_b006a.py:1167` | `runpy.run_path(..., run_name="__main__")` | `sys.argv` is patched via monkeypatch, and runpy restores `__main__`. |
| `test_isolation.py:1619/1632` (`os.umask`), `:1669/1680` (`RLIMIT_NOFILE`) | Process-wide settings | Restored in `finally`. The fd limit is derived from the current fd count, so earlier leaks don't break it. |
| `test_mutation_state_crash_tails.py:287-337`, `test_mutation_judge_identity.py:1098` | `object.__setattr__` | Applied to a locally built `CommandResult.__new__`. |
| `test_liveness_runner_monitor.py:435,481,531,565` | Sets `runner._sleep` and similar | Attributes of a local `LivenessRunner` instance. |
| Environment, cwd, signals, logging | — | No `os.environ[...] =`, `.update`, `putenv`, `os.chdir` (all use `monkeypatch.chdir`), `signal.signal`, logging config, `cache_clear()`, module-level mutable test state (AST scan: 0), or `global` in tests. |
| Writes into the source tree | None | `test_distribution_gate.py:933-934` only symlinks `PROJECT_ROOT/src/assay` and `tests` into a temp dir for a read-only pyflakes run. Git use on `REPO_ROOT` is read-only: `git status` (`test_python_qualification.py:62`, which may refresh index stat data only) and `clone --no-local` into tmp (`build_release.py:172`). Tag, worktree and commit operations only touch temp repos (`test_isolation.py:948,1421`; `test_measurability_base_is_head.py:55`; `test_mutation_judge_identity.py:998`). |
| Leftovers in the snapshot's working directory | `.pytest_cache/` (the lane argv doesn't disable the cache provider) and `.hypothesis/` (`test_mutation_judge_identity_properties.py:46-47` uses `database=None, derandomize=True`) | Both are gitignored (`assay/.gitignore:11-12`). No `--lf/--ff` is used, so there is no effect across runs. |
| Fixed `/tmp` paths | `test_b105_mutation_boundaries.py:337` uses `/tmp/state`; `test_runner_result_report.py:822,851` use `cwd=/tmp` with `sh -c "exit 0"` | `/tmp/state` is rejected by validation before use; only a mutant that bypasses the validation could write there, which could leak between candidates in one container (low). The `/tmp` runs write nothing. |
| Leftover processes | `test_liveness_proc_helpers.py:97,172` `sh -c "sleep 2 & wait"` | `kill()` on the parent leaves a sleep orphaned for at most 2 s. The child-scan tests check membership, not equality. Low. |

#### Shared fixtures (session or module scope)

| Fixture | Defined at | Used by | Mutated? | Notes for re-ordering or splitting |
|---|---|---|---|---|
| `schema` / `validator` (session) | `conftest.py:1356/1362` | `test_verdict_serialises.py:384`; `test_verdict_timestamp_agreement.py:75` (reads a sub-schema) | No (AST scan) | The dict could be mutated by a future test; hand out a deepcopy if you want to harden it. |
| `standalone` (session) | `conftest.py:1431-1497` | `test_analysis.py:721` (the **first** user alphabetically, so it pays the wheel+venv build in its setup phase, which the call-duration evidence doesn't show), `test_dependency_purity.py` ×4, `test_go_helper_is_packaged.py` ×3, `test_standalone.py` ×18, `test_verdict_schema_is_packaged.py` ×5 | No: `test_standalone.py:24` and `:1932` build private venvs instead | Split into separate processes, every process with a user rebuilds it. Moving `test_standalone.py` to a slow tier does **not** remove the build from the fast tier while `test_analysis.py:721` and the three packaging modules still use it. It is built from `PROJECT_ROOT/src`, so these tests do see the mutant. |
| `built` (module) | `test_distribution_build_release.py:362` | 13 tests in that file | No | `test_a_build_writes_nothing_outside_its_own_outdir` (:391) expects the enclosing dir to hold exactly `["dist"]`; it would break if a future test wrote next to the outdir. |
| `gate_functions` / `lint_venv` (session) | `test_distribution_gate.py:51/690` | Only that file | No; users `copytree` first (:802, 837, 855, 894, 913, 935) | — |
| `installed_assay`, `docker`/`dstdns_checkout`, `timestamp_validator` (module) | `test_python_qualification.py:550`; `test_gate_qualify_dstdns_sql.py:103/113`; `test_verdict_timestamp_agreement.py:74` | — | Immutable or skipped | — |

#### Reliance on `conftest` module identity
- 132 test modules plus `tests/qualification/test_javascript_real_vitest.py:37` do `from conftest import …`. `test_self_lane.py:22` imports the `pytest_sessionfinish` hook itself.
- This works because pytest's default "prepend" import mode registers `tests/conftest.py` as the top-level module `conftest` (there is no `__init__.py` in `tests/` or `tests/qualification/`).
- Constraints for a re-layout:
  - No second `conftest.py` anywhere under `tests/`. It would collide in `sys.modules['conftest']`, as already noted at `conftest.py:342-347`.
  - Test file basenames must stay unique across subdirectories.
  - Running `pytest tests/slow` on its own still loads the parent `tests/conftest.py`, so `from conftest import` keeps working.
- The session-end hook (`conftest.py:59`) reads the `ASSAY_B105_*` variables. `test_self_lane.py:258-375` sets them only through `monkeypatch`, so they are restored before the hook runs. Benign.

### Summary
- `src/assay` has no caches, no mutated module state and no import-time environment capture, so it cannot cause order-dependence.
- The one real hazard is the liveness leak between the suite and the outer plugin at `test_liveness.py:570-606` and `:656/:803`. It already undercounted two tests in the baseline evidence, and it can turn a later long test into a false HUNG candidate. Fix it before re-tiering the suite.