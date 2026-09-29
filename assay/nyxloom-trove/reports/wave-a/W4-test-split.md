# W4 — Judge tests vs tooling tests, and the same-commit release-gate binding (B123)

| Field | Value |
|---|---|
| Backlog | **B123** (A-476, A-468(c)) |
| Branch | `wave-a-w4-test-split` from `assay-b110-landing` (own worktree, `--no-ff`) |
| Depends on | **W3**, **W1** and **W2** must be merged. Judge tests stay in W3's `tests/<component>/`; re-resolve anchors **by file name**. Leave the dstdns SQL test to W5, which runs in parallel. W8 extends this CLI later |
| Contract class | **2c**: integration against fixed contracts |
| Implementer | **Sonnet**, fresh session (operator rule) |
| Decisions | A-476, A-468(c), A-130 (the wheel lane keeps `--override-ini=pythonpath=`), A-475 |

## Carver questions (answer before dispatch; the recommendation is assumed)
| # | Recommendation |
|---|---|
| C1 | S1 binds only the full `self-qualification` lane; a standalone preflight needs no receipt |
| C2 | The two static sweeps (`untrusted_json_parse`, `result_report_wiring`) and W3's import-contract test stay judge: they are source rules. Cache them (R9 RC8) |
| C3 | The receipt means the latest registered run at this commit passed; it is cleared at launch |
| C4 | `test_self_lane.py` moves to `gate/tests/`; plan §3 (W7) must follow |
| C5 | A non-gate test that still requests `standalone` after W2 (the FIFO test, ex `test_analysis.py:721`) runs `python -m assay analyze` from source |

## Context to read first
| Read (at `5bbd916e`, relative to `assay/`) |
|---|
| plan §2–§4; `decisions.md:1021` (A-476), `:968` (A-468), `:393` (A-130); R9 §1 and §3 |
| `tools/tester-unified-gate.sh:134-152,364-370,791-821`; `tools/self-qualification-gate.sh:24-67,156-214`; `tools/b105_report_check.py` |
| `assay.toml:17-44,65-90,185-198`; `pyproject.toml:94-97`; `src/assay/mutation_witness.py:60-63` |
| `tests/conftest.py:52-59,276-335,501-565,1356-1370,1389-1497`; `tests/test_self_lane.py`; `tests/test_distribution_gate.py:52-95,745-940` |

## Implementation packet (normative)

### P1. Classification
**Judge**: the outcome depends on `src/assay` running (in-process or from snapshot source). **Tooling**: tests of `tools/`, `gate/`, the wheel or zipapp, packaging, or gate-config drift.

**Tooling.** Move each whole file with `git mv` to `gate/tests/`, keeping its name:

| Files | Why |
|---|---|
| `test_b105_report_check`, `test_cgroup_parent`, `test_gate_failure_diagnostics`, `test_distribution_gate` | checker and gate script |
| `test_distribution_build_release`, `test_distribution_release_wheel` | release builder and zipapp |
| `test_dependency_purity`, `test_go_helper_is_packaged` | distribution properties |
| `test_self_hosting` | the gate witness (A-130) |
| `test_self_lane` | config drift (C4) |
| `test_standalone` | wheel R0–R3 runs and packaging negatives. **Do not split**: logic is covered in-process; layout branches go to P5 |
| `qualification/test_go_r1_real`, `qualification/test_javascript_real_vitest` | move to `gate/tests/qualification/` (opt-in real toolchains, A-350) |

**Split** `test_verdict_schema_is_packaged.py`: keep `:64` and `:163`; move `:134`, `:171`, `:189`, `:209`, `:240` and `:258` into a new `gate/tests/test_verdict_schema_wheel.py`.

**Judge (stays in W3's layout).** Every other `tests/**/test_*.py` present at `5bbd916e` is judge: 225 files, plus the kept split half and C2. Families and counts:

| Family | Count | Family | Count |
|---|---|---|---|
| adapters | 30 | git | 7 |
| coverage | 23 | verify, canary | 6 each |
| runner, mutation | 22 each | cli, attestation | 5 each |
| config | 21 | liveness | 4 |
| verdict | 19 | diff, lane, isolation, adjudication | 3 each |
| b105 (not report_check) | 13 | safeio, measurability | 2 each |
| evaluate | 10 | singles | 16 |

A file added since `5bbd916e` (`git diff --diff-filter=A 5bbd916e -- tests/`), other than W3's import-contract test, is BLOCKED. `test_gate_qualify_dstdns_sql` belongs to W5 and counts as tooling under this rule.

### P2. `gate/tests` is a package
Why: with a second `conftest.py`, `from conftest import` gets whichever loaded last (pytest 9.1.1; reproduced as an `ImportError`). Add empty `__init__.py` files to `gate/`, `gate/tests/` and `gate/tests/qualification/`, but never to `assay/` or `tests/`. Import point `gate/tests/support.py`:
```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]; REPO_ROOT = PROJECT_ROOT.parent
def _load_judge_conftest():                  # tests/conftest.py under a unique name
    name = "assay_judge_conftest"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / "tests" / "conftest.py")
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]
judge = _load_judge_conftest()
# re-export GitRepo, why_invalid, runner_verdict_fixture, verdict_fixture, SCHEMA_PATH;
# b105_coverage_sessionfinish = judge.pytest_sessionfinish; move here verbatim:
# _parent_repository_toplevel, _PARENT_TOPLEVEL, requires_parent_repository,
# _build_backend_home, _clean_env, Standalone
```
| File | Rule |
|---|---|
| `gate/tests/conftest.py` | `from gate.tests.support import judge`; `git_repo`, `schema`, `validator` = `judge.<name>`; `standalone` moved verbatim (`tests/conftest.py:1431-1497`). No star import; `pytest_sessionfinish` appears in no `gate/tests` file |
| moved modules | `from conftest import X` → `from gate.tests.support import X`; `Path(__file__)…parents[1]` → `PROJECT_ROOT`; `test_go_r1_real.py:54` `parents[2]` → `parents[3]` |
| `tests/conftest.py` | delete a moved definition only when `git grep -nw <name> -- tests/` finds no judge user |

### P3. Lanes, pyproject, gate script

| Where | New value |
|---|---|
| `assay.toml` `tester-unified` | `["python","-m","pytest","tests","gate/tests","-q","--ignore=gate/tests/test_self_hosting.py","--override-ini=pythonpath="]` (comment `:30-43`) |
| both B105 lanes | drop `--ignore=tests/test_self_hosting.py` and `--override-ini=pythonpath=src`, leaving `["python","-m","pytest","tests","-q","--cov=src/assay","--cov-branch","--cov-report=json:.assay/coverage-<lane>.json"]`. Keep W2's tokens and keep the lanes identical (comment `:65-77`) |
| `pyproject.toml:94-95` comment | `pythonpath = ["src"]` is pinned and load-bearing (A-468(c)). The override must go because `mutation_witness.py:60-63` makes any `-o` token witness-ineligible, and B110 P3b needs eligibility |
| `run_independent_witness` `:367` | `gate/tests/test_self_hosting.py` |
| `run_lint_phase` | add a second array from `find -H "$scratch/clone/assay/gate/tests" -type f -name '*.py' -print0`; if it is empty, `die 'lint phase found no gate/tests sources to lint'`; lint both (comment `:111-133`) |

### P4. S1: same-commit receipt
| Part | Specification |
|---|---|
| Evidence today | `COMPLETE=1` is stdout-only (`:821`); the verdict dies with the container; `.run-gate/history.json` is telemetry |
| Receipt | path `<worktree>/assay/.assay/registered-gate/tester-unified.json` (ignored by `vbpub/.gitignore:343`). One line exactly: `{"schema_version": 1, "lane": "tester-unified", "commit": "<hex>", "tree": "<hex>"}` |
| `clear_registered_gate_receipt W` | `rm -f` the receipt |
| `write_registered_gate_receipt W C T` | die unless C and T each match `^[0-9a-f]{40}([0-9a-f]{24})?$`; write through `mktemp` in the same directory, then `mv -f` |
| `finish_registered_gate W C T` | die `HEAD changed during the registered gate; no receipt` unless `git -C W rev-parse HEAD` = C and `HEAD^{tree}` = T. Then write the receipt and echo `ASSAY_REGISTERED_GATE_RECEIPT=<path>`, then `ASSAY_REGISTERED_GATE_COMPLETE=1` |
| Entry section | the three functions sit above `# --- entry points`. Capture C and T, then clear → `run_registered_tester_container` → finish. Delete the bare echo at `:821` |
| `verify_tester_unified_receipt(document, *, expected_commit, expected_tree)` | raise `ValueError` unless: it is a dict with exactly the four keys; `type(schema_version) is int` and it `== 1`; `lane == "tester-unified"`; commit and tree equal the expected values |
| Checker CLI | `--receipt-only` accepts only the receipt, commit and tree args (anything else is `parser.error`); success prints `B105_TESTER_UNIFIED_PASS=commit=<c> tree=<t>` and exits 0. In full mode `--tester-unified-receipt` is required for lane `self-qualification`, forbidden for other lanes (C1), and checked before `verify_report_document`. Every refusal (a missing file too) prints `B105_REPORT_REJECTED=<reason>` and exits 2 |
| Driver | `receipt="$project/.assay/registered-gate/tester-unified.json"`. Lane `self-qualification` only, between `:60` and `:67`: echo `B105_PHASE=require-same-commit-tester-unified-pass`, then run `--receipt-only` or `die "no registered tester-unified pass at $source_commit; run ./run-gate.py tester-unified first"`. `run_and_verify_lane` passes the receipt for `self-qualification` only |

### P5. Layout-dependent product code
| Code | Stand-in check |
|---|---|
| `provenance.py` `_zipapp_archive` (104-118), `_installed_wheel_digest` (120-191), `identify_judge` (193-320; seams `module=`, `dist=`) | `test_cli_provenance_and_request_base.py:138-379` and `test_b105_provenance_boundaries.py:32-120`, run alone with `--cov=assay.provenance --cov-branch`, must give 100% line+branch; a missing arc gets a new stand-in in the second file |
| `go_stmtpos.py` `_staged_helper` (124-171), one `importlib.resources` path | `test_b105_go_stmtpos_boundaries.py` alone, `--cov=assay.adapters.go_stmtpos`: no missing line in 124-171 |
| `__init__.py:42-45` | covered by judge `test_b105_source_coverage_controls.py:38` |

## Work
| # | Step |
|---|---|
| 1 | Apply P1–P3. Record the final P1 table, with paths after W3, in the REPORT |
| 2 | Apply P4 and add its tests (O5–O7) |
| 3 | `gate/tests/test_self_lane.py` pins: exact argv per lane; no `-o`/`--override-ini*`/`--ignore*`/`--deselect*`/`-p*`; ini keys exactly `pythonpath=["src"]`, `testpaths=["tests"]`; no `pytest.ini`/`.pytest.ini`/`tox.ini`/`setup.cfg` in `PROJECT_ROOT` or `tests/`; the P4 driver text |
| 4 | Lint tests: give each synthetic clone a `gate/tests` file; add a planted-finding case and a missing-tree case |
| 5 | Run the P5 checks, then update the docs, decisions and backlog |

## Oracles
Each break is applied locally, observed red, reverted and logged; never committed.

| # | Observable | Break / negative |
|---|---|---|
| O1 | collect-only succeeds for `tests gate/tests`, `gate/tests tests` (same count) and `gate/tests/test_self_hosting.py` | delete `gate/tests/__init__.py` → error |
| O2 | `pytest tests --collect-only` needs nothing from `gate/` | a judge test that requests `standalone` → error |
| O3 | Work 3 pins pass | re-add `--override-ini=pythonpath=src` → red; add `addopts` → red |
| O4 | lint covers both trees | an unused `import os` planted in a synthetic `gate/tests` file → no `pyflakes-clean` |
| O5 | the checker accepts an exact receipt | refused: missing file, other commit, other tree, lane `self-qualification`, `schema_version` `True`/`2`, extra key, missing key, non-object |
| O6 | **S1**: a valid full report plus another commit's receipt exits 2 (`B105_REPORT_REJECTED`); so does no receipt | the receipt check returns early → red |
| O7 | `run_bash`: exact JSON; bad hex → fail, no file; `finish_…` after HEAD moved → fail, no receipt or COMPLETE; order clear<run<finish pinned; one COMPLETE echo | write before the HEAD check → red |
| O8 | gate green; P5 passes; `ASSAY_REGISTERED_GATE_RECEIPT=` printed; receipt commit = HEAD. The B105 lanes are W7's to prove (plan §3) | — |

**Forbidden in any test you write (AUTHORING §3b, condensed):**
| Rule | Forbidden |
|---|---|
| A | a `monotonic()+N` deadline followed by an assert; `sleep` used to wait; asserts on elapsed time or iteration counts. A timeout is only a generous failsafe, and a slow-host red is a true red: never widen a timeout |
| B | unrestored global state (`os.environ`, module attributes); `monkeypatch` on `__getattr__` proxies; destructive teardown |
| C | hollow tests (`pass`, "nothing raised", call counts, private attributes, log strings); weakening an assertion |
| D–F | no-cover pragmas (even in comments); real network, clock or registries; predicted coverage or mutation numbers |

## Heavy judge tests left (record for A-468(b); from R8/R9)
| Heavy judge tests |
|---|
| `test_cli_run.py:463/586/677/1720`; `test_b106_reuse_and_witness.py:680/787/820/926/1001`; `test_runner_run_lane_r3`; `test_canary_python_pipeline`; `test_progress_phase_stream:456`; `test_lane_timeout_writes_a_verdict`; `test_environment_preflight:128/246`; `test_runner_result_report:450`; `git_repo` setup; C2 JSON sweep |

## Docs, decisions, backlog
| Where | Edit |
|---|---|
| README `:977`; DESIGN-GUIDE §9 and B105 (`:1972-2062`) | two trees; B105 collects `tests/` only; S1 |
| CONSUMERS B105 (`:59-144`) | run `./run-gate.py tester-unified` first |
| CONSUMERS `:1426` | the vitest file's new path |
| `CHANGES.md` | an `[Unreleased]` entry |
| decisions (notes only) | A-468 ((a) superseded, (c) done); A-130 and A-350 (paths) |
| backlog | B123 DONE; an S1 bullet in B105 acceptance (`:10716`) |

## Scope
| Touch | Forbid |
|---|---|
| `tests/**`, `gate/**` (only `__init__.py`, `tests/**`), the 3 `tools/` files, `assay.toml`, the pyproject comment, the docs, `nyxloom-trove/*.md`, `nyxloom.toml`, `W4-*` | `src/assay/**`, `gate/{python,distribution}/**`, `run-gate.toml`, `carve-assets/**`, the dstdns SQL test (W5), `snapshot_history` (W7), `zz_slow` |

## Gate
```bash
docker ps   # no other session's gate container may be running
cd <worktree>/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > ../W4-gate.log 2>&1; echo "exit=$?"
```
Read the markers and `FAILED` lines in a **separate** step.

**Known red (plan §4):** `tests/test_cli_run.py::test_run_liveness_classifies_a_thread_join_hang_as_hung` (B107; `612843ef`). Accept the gate only if this is the sole failure in the log; record it in the LOG.

| Host-load rule (a production game server shares this host) |
|---|
| Run nice/ionice serial pytest only. At most one gate container, and none while another session's gate runs. Never the full `self-qualification` lane. Remove containers by exact name only. Edit with the editor tools (`git mv` is fine). Trailer `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>` |

## BLOCKED rule
Write `BLOCKED: <reason>` to `W4-LOG.md`, commit and stop if: W3, W1 or W2 is unmerged; a `tests/` file fits no P1 row; a helper is missing; `src/assay` behaviour would change; a C-question is open; or the gate is red beyond the known test. Never improvise.

## Review
A fresh-session adversarial reviewer checks git state before merge:
- re-derive P1;
- collect in both orders;
- forge stale receipts;
- confirm that the wheel lane keeps `--override-ini=pythonpath=`.
