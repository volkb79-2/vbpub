# W4 — Judge tests vs tooling tests, and the same-commit release-gate binding (B123)

| Field | Value |
|---|---|
| Backlog | **B123** (A-476, A-468(c)) |
| Branch | `wave-a-w4-test-split` from the current `assay-b110-landing` tip (>= `be803c3a`; own worktree, `--no-ff`) |
| Depends on | **W3**, **W1**, **W2** merged (stage 2 order W6, W4, W5; W5 branches after W4, CD28). Re-resolve `tests/` anchors **by file name** (`git ls-files ':(glob)tests/**/<name>'`). W8 extends this CLI later |
| Contract class | **2c**: integration against fixed contracts |
| Implementer | **Sonnet**, fresh session (operator rule) |
| Decisions | A-476, A-468(c), A-130 (the wheel lane keeps `--override-ini=pythonpath=`), A-475; `CARVER-DECISIONS.md` CD9-CD15, CD23, CD29 |

## Carver questions (all decided; `CARVER-DECISIONS.md` binds)
| # | Decision |
|---|---|
| C1 | Decided (CD9): S1 binds only the full `self-qualification` lane |
| C2 | Decided (CD10): the static sweeps and W3's import-contract test stay judge. No caching: file R9 RC8 as a follow-up under B123/A-468(b) |
| C3 | Decided (CD11 amended): the receipt is exactly P4's four-key document; cleared at launch |
| C4 | Decided (CD12): `test_self_lane.py` moves to `gate/tests/`; W7 edits it there |
| C5 | Decided (CD13 amended): **W2** owns the FIFO test; W4 only verifies `git grep -nw standalone -- analysis/` is empty |

## Context to read first
Anchors at `be803c3a`, relative to `assay/` unless prefixed:
- `nyxloom-trove/reports/wave-a/CARVER-DECISIONS.md` (whole file); plan §2–§4; `nyxloom-trove/decisions.md:1021` (A-476), `:968` (A-468), `:393` (A-130); R9 §1, §3
- `tools/tester-unified-gate.sh:134-152,364-370,791-821`; `tools/self-qualification-gate.sh:24-67,156-214`; `tools/b105_report_check.py`
- `assay.toml:17-44,65-90,185-198`; `pyproject.toml:96-100` (shifted by W2); `src/assay/mutation_witness.py:60-63`
- `tests/conftest.py:52-59,276-335,501-565,1356-1370,1389-1499`; `tests/test_self_lane.py`; `tests/test_distribution_gate.py:52-95,745-940`; W2's `analysis/tests/conftest.py`

## Implementation packet (normative)

### P1. Classification
**Judge**: the outcome depends on `src/assay` running (in-process or from snapshot source). **Tooling**: tests of `tools/`, `gate/`, the wheel or zipapp, packaging, or gate-config drift.

**Tooling.** Move each whole file with `git mv` to `gate/tests/`, keeping its name:

| Files | Why |
|---|---|
| `test_b105_report_check`, `test_cgroup_parent`, `test_gate_failure_diagnostics`, `test_distribution_gate` | checker and gate script |
| `test_distribution_build_release`, `test_distribution_release_wheel`, `test_dependency_purity`, `test_go_helper_is_packaged` | release builder, zipapp, distribution properties |
| `test_self_hosting`; `test_self_lane` | the gate witness (A-130); config drift (C4) |
| `test_standalone` | wheel R0–R3 and packaging negatives. **Do not split**: layout branches go to P5 |
| `qualification/test_go_r1_real`, `qualification/test_javascript_real_vitest` | to `gate/tests/qualification/` (opt-in toolchains, A-350) |
| `test_gate_qualify_dstdns_sql` | reads `gate/python` fixtures: move whole, path rules only, so W5 (after W4) edits it there |

**Split** `test_verdict_schema_is_packaged.py`: keep `:64` and `:163` (its import line becomes `from conftest import PROJECT_ROOT, SCHEMA_PATH`, no `Standalone`); move `:134`, `:171`, `:189`, `:209`, `:240` and `:258` into a new `gate/tests/test_verdict_schema_wheel.py`.

**Finish the layout (CD23 amended).** `git mv` every judge file still at the `tests/` root into `tests/core/` (its `expected_dir`): `test_runner_snapshot_selection`, `test_lane_schema_v2_locked_successors`, `test_verdict_v13_successors`, `test_b106_reuse_and_witness`, `test_verdict_conformance`, `test_errors`, the kept split half, and any other root file W1/W2/W5 left. Apply W3's 2c path rewrites to real code (`test_verdict_v13_successors.py:19` `parents[1]` → `PROJECT_ROOT`); leave `test_b106_reuse_and_witness.py`'s generated-code string literals alone. Then delete `ROOT_PINNED` and its `expected_dir` branch from `tests/core/test_import_contracts.py`. Afterwards `tests/` root holds only `conftest.py`, `fixtures/` and support files; layout checks exclude `tests/fixtures/`.

**Judge (W3's layout).** Every other `tests/**/test_*.py` is judge (225 files at `5bbd916e`, plus the kept split half and C2). So is every file added under `tests/` since then (`git diff -M --diff-filter=A --name-only 5bbd916e -- tests/`) unless this table names it; list each in `W4-REPORT.md`. Expected: W3 `tests/core/test_import_contracts.py`; W2 `tests/core/test_cli_analyze_seam.py`; W6's per-component guard tests if merged first. BLOCKED only if an added file imports `gate`/`gate.tests`, requests `standalone`, or reads `tools/`, `gate/`, a wheel or a zipapp.

### P2. `gate/tests` is a package
Why: with a second `conftest.py`, `from conftest import` gets whichever loaded last (reproduced as an `ImportError`). Add empty `__init__.py` to `gate/`, `gate/tests/` and `gate/tests/qualification/`, never to `assay/` or `tests/`. Import point `gate/tests/support.py`; reuse W2's loader in `analysis/tests/conftest.py` (CD13/CD14: same module name `assay_judge_conftest` via `spec_from_file_location`, never load `tests/conftest.py` twice under different names):
```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]; REPO_ROOT = PROJECT_ROOT.parent
judge = <W2's loader result for PROJECT_ROOT / "tests" / "conftest.py">
# re-export GitRepo, why_invalid, runner_verdict_fixture, verdict_fixture, SCHEMA_PATH;
# b105_coverage_sessionfinish = judge.pytest_sessionfinish; move here verbatim:
# _parent_repository_toplevel, _PARENT_TOPLEVEL, requires_parent_repository,
# _build_backend_home, _clean_env, Standalone
```
| File | Rule |
|---|---|
| `gate/tests/conftest.py` | `from gate.tests.support import judge`; `git_repo`, `schema`, `validator` = `judge.<name>`; `standalone` moved verbatim (`tests/conftest.py:1431-1499`). No star import; no `gate/tests` module binds a top-level name `pytest_sessionfinish` |
| moved modules | `from conftest import X` → `from gate.tests.support import X`; `Path(__file__)…parents[1]` → `PROJECT_ROOT`, **except two opt-in files** (skipped in every gate; O9 guards them): `qualification/test_javascript_real_vitest.py` `_PROBE_JS = PROJECT_ROOT / "tests" / "fixtures" / "coverage" / "probe-js"` (old `parents[1]` was `tests/`); `test_go_r1_real.py` `_PROJECT_ROOT = PROJECT_ROOT` (from support) instead of `parents[3]` |
| `tests/conftest.py` | run the grep below after W1/W2: it must be empty, else BLOCKED. Then delete all those names from `tests/conftest.py` (support.py holds the moved copies) |

`git grep -nwE '(_parent_repository_toplevel|_PARENT_TOPLEVEL|requires_parent_repository|_build_backend_home|_clean_env|Standalone|standalone)' -- tests/ ':!tests/conftest.py'`

### P3. Lanes, pyproject, gate script

| Where | New value |
|---|---|
| `assay.toml` `tester-unified` | `["python","-m","pytest","tests","gate/tests","-q","--ignore=gate/tests/test_self_hosting.py","--override-ini=pythonpath="]` (comment `:30-43`) |
| both B105 lanes | drop `--ignore=tests/test_self_hosting.py` and `--override-ini=pythonpath=src`, leaving `["python","-m","pytest","tests","-q","--cov=src/assay","--cov-branch","--cov-report=json:.assay/coverage-<lane>.json"]`; the lanes stay identical (comment `:65-77`) |
| `pyproject.toml` comment (`:96-97`) | `pythonpath = ["src", "analysis/src"]` (CD15) and `testpaths = ["tests", "analysis/tests"]` (set by W2) are pinned and load-bearing (A-468(c)). The override must go: `mutation_witness.py:60-63` makes any `-o` token witness-ineligible, and B110 P3b needs eligibility |
| `run_independent_witness` `:367` | `gate/tests/test_self_hosting.py` |
| `gate/tests/test_self_hosting.py` | `DECLARED_LANE_ARGV` (hand-transcribed, `:80-88`; asserted at `:171`) = exactly the `tester-unified` argv above |
| `run_lint_phase` | add one more array from `find -H "$scratch/clone/assay/gate/tests" -type f -name '*.py' -print0`; if it is empty, `die 'lint phase found no gate/tests sources to lint'`; lint all (comment `:111-133`) |

### P4. S1: same-commit receipt
| Part | Specification |
|---|---|
| Receipt | path `<worktree>/assay/.assay/registered-gate/tester-unified.json` (ignored by `vbpub/.gitignore:343`), exactly the CD11-amended four-key line below (no version or marker fields: the host script cannot see them). Today `COMPLETE=1` is stdout-only (`:821`) |
| `clear_registered_gate_receipt W` | `rm -f` the receipt |
| `write_registered_gate_receipt W C T` | die unless C and T each match `^[0-9a-f]{40}([0-9a-f]{24})?$`; `mkdir -p "$W/assay/.assay/registered-gate"`; `printf '{"schema_version": 1, "lane": "tester-unified", "commit": "%s", "tree": "%s"}\n' "$C" "$T" > "$tmp"` (`mktemp` in that directory); `chmod 0644 "$tmp"` (the container reads it); `mv -f` |
| `finish_registered_gate W C T` | die `HEAD changed during the registered gate; no receipt` unless `git -C W rev-parse HEAD` = C and `HEAD^{tree}` = T. Then write the receipt, echo `ASSAY_REGISTERED_GATE_RECEIPT=<path>`, then literally `echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'` (W1's test asserts that exact string) |
| `run_registered_gate W HOST CG` | a fourth function above the marker. **First (CD32):** `names="$(docker ps --no-trunc --format '{{.Names}}' | grep '^run-gate-' | paste -sd, -)" || true`; if non-empty, `echo "ASSAY_GATE_INCONCLUSIVE=host busy — rerun: $names" >&2; exit 3` (the receipt is untouched; no waiting). Then capture C and T; clear; `run_registered_tester_container "$W" "$HOST" "$CG"`; `finish_registered_gate "$W" "$C" "$T"`. The entry section's last line is `run_registered_gate "$worktree" "$host_repo_root" "$cgroup_parent"`. Delete the bare echo at `:821` |
| `verify_tester_unified_receipt(document, *, expected_commit, expected_tree)` | `ValueError` unless: dict with exactly the four keys; `type(schema_version) is int` and `== 1`; lane `tester-unified`; commit and tree exactly equal the expected values |
| Checker CLI | `--receipt-only --tester-unified-receipt P --expected-commit C --expected-tree T`. Drop `required=True` from the nine existing flags: in full mode `parser.error` names each missing flag; in receipt-only mode it fires if any other flag is set. Success: stdout `B105_TESTER_UNIFIED_PASS=commit=<c> tree=<t>`, exit 0. Full mode: `--tester-unified-receipt` required for lane `self-qualification`, forbidden for others (C1), checked before `verify_report_document`. Every refusal (missing file too): stderr `B105_REPORT_REJECTED=<reason>`, exit 2 |
| Driver | `receipt="$project/.assay/registered-gate/tester-unified.json"`. Insert right after `self-qualification-gate.sh:60`, for lane `self-qualification` only: echo `B105_PHASE=require-same-commit-tester-unified-pass`, then run `"$tester_python" "$scratch/source/assay/tools/b105_report_check.py" --receipt-only --tester-unified-receipt "$receipt" --expected-commit "$source_commit" --expected-tree "$source_tree"`, on failure `die "no registered tester-unified pass at $source_commit; run ./run-gate.py tester-unified first"`. `run_and_verify_lane` passes the receipt for `self-qualification` only |

### P5. Layout-dependent product code
| Code | Stand-in check |
|---|---|
| `provenance.py` `_zipapp_archive` (104-118), `_installed_wheel_digest` (120-191), `identify_judge` (193-320; seams `module=`, `dist=`) | whole files `test_cli_provenance_and_request_base.py` and `test_b105_provenance_boundaries.py` (in `tests/core/`): run `PYTHONPATH=src nice -n 19 python3 -m pytest tests/core/test_cli_provenance_and_request_base.py tests/core/test_b105_provenance_boundaries.py -q -p no:cacheprovider --cov=assay.provenance --cov-branch --cov-report=term-missing`; 100% line+branch required. A missing line or arc is BLOCKED (the carver specifies the stand-in) |
| `go_stmtpos.py` `_staged_helper` (124-171) | `test_b105_go_stmtpos_boundaries.py` alone, `--cov=assay.adapters.go_stmtpos`: no missing line in 124-171 |
| `__init__.py:42-45` | judge `test_b105_source_coverage_controls.py:38` |

## Work
| # | Step |
|---|---|
| 1 | Apply P1–P3 (incl. finishing the layout). Record the final P1 table, with paths after W3, in the REPORT |
| 2 | Apply P4; add O5 and O6 to `gate/tests/test_b105_report_check.py`, O7 to `gate/tests/test_distribution_gate.py`, O2's AST check to `tests/core/test_import_contracts.py` (section 3, "judge/tooling separation") |
| 3 | `gate/tests/test_self_lane.py` pins: exact argv per lane; no `-o`/`--override-ini*`/`--ignore*`/`--deselect*`/`-p*` in the B105 lanes; ini keys exactly `pythonpath == ["src", "analysis/src"]` and `testpaths == ["tests", "analysis/tests"]` (`gate/tests` is deliberately absent: the lane names it); no `pytest.ini`/`.pytest.ini`/`tox.ini`/`setup.cfg` in `PROJECT_ROOT` or `tests/`; the P4 driver text; keep the `snapshot_history == "full"` pin (W7 flips it) |
| 4 | Lint tests: give each synthetic clone a `gate/tests` file; add a planted-finding case and a missing-tree case |
| 5 | Run the P5 command; update docs, decisions, backlog; `nyxloom.toml:125-137` prose path → `gate/tests/test_self_hosting.py`, `:148-149` "four inner phase markers" → the actual count |

## Oracles
Each break is applied locally, observed red, reverted and logged; never committed.

| # | Observable | Break / negative |
|---|---|---|
| O1 | collect-only succeeds for `tests gate/tests`, `gate/tests tests` (same count) and `gate/tests/test_self_hosting.py` | delete `gate/tests/__init__.py` → error |
| O2 | the AST check (Work 2) over every `tests/**/test_*.py` outside `tests/fixtures/` passes: no `import gate…`/`from gate…`, no parameter named `standalone`, no `from conftest import` of `Standalone`, `_clean_env`, `_build_backend_home` or `requires_parent_repository` (`--collect-only` cannot see missing fixtures) | plant `def test_x(standalone): assert standalone` → red naming the file |
| O3 | Work 3 pins pass | re-add `--override-ini=pythonpath=src` → red; add `addopts` → red; set `pythonpath = ["src"]` → red |
| O4 | lint covers both trees | an unused `import os` planted in a synthetic `gate/tests` file → no `pyflakes-clean` |
| O5 | the checker accepts an exact receipt | refused: missing file, other commit, other tree, uppercase hex of the right commit, right commit plus 24 extra hex digits, lane `self-qualification`, `schema_version` `True`/`2`, extra key, missing key, non-object |
| O6 | **S1**: a valid full report plus another commit's receipt exits 2 (`B105_REPORT_REJECTED`); so does no receipt | the receipt check returns early → red |
| O7 | `run_bash`: exact JSON, mode 0644; bad hex → fail, no file; `finish_…` after HEAD moved → fail, no receipt or COMPLETE; with `run_registered_tester_container() { return 7; }` stubbed after sourcing, `run_registered_gate` exits non-zero with no receipt and no COMPLETE; one COMPLETE echo | write before the HEAD check → red; a `true`-swallowing OR after the run call → red |
| O7a | `run_bash` with a PATH stub `docker` whose `ps` prints `run-gate-x` (CD32): `run_registered_gate` exits 3; stderr has `ASSAY_GATE_INCONCLUSIVE=host busy — rerun: run-gate-x`; a pre-existing receipt is byte-identical afterwards; the stub's log shows no `docker` call other than `ps`. With `ps` printing `other-container`, the run proceeds to the (stubbed) tester | drop the check → red; place it after `clear` → red (receipt gone) |
| O8 | gate green; P5 passes; `ASSAY_REGISTERED_GATE_RECEIPT=` printed; receipt commit = HEAD; `git grep -nw standalone -- analysis/` empty | — |
| O9 | `gate/tests/test_self_lane.py` asserts `(…test_javascript_real_vitest._PROBE_JS / "package.json").is_file()` and `(…test_go_r1_real._PROJECT_ROOT / "pyproject.toml").is_file()` (module paths `gate.tests.qualification.*`) | restore `parents[1]` → red |
| O10 | after the gate, once: `nice -n 19 ionice -c3 python ./run-gate.py self-qualification-preflight` (plan §4; CD9: no receipt needed): checker `B105_REPORT_ACCEPTED`, R1 PASS. Record collected/passed/skipped in `W4-REPORT.md` as W7's baseline | — |

**Forbidden in any test you write (AUTHORING §3b):** A. `monotonic()+N` deadlines then assert, `sleep` to wait, elapsed-time or iteration-count asserts (a timeout is only a generous failsafe; never widen one). B. unrestored global state, `monkeypatch` on `__getattr__` proxies, destructive teardown. C. hollow tests (`pass`, "nothing raised", call counts, private attributes, log strings); weakened assertions. D-F. no-cover pragmas (even in comments); real network, clock or registries; predicted coverage or mutation numbers.

## Heavy judge tests left (record for A-468(b), by name)
`test_cli_run` heavy cases, `test_b106_reuse_and_witness`, `test_runner_run_lane_r3`, `test_canary_python_pipeline`, `test_progress_phase_stream`, `test_lane_timeout_writes_a_verdict`, `test_environment_preflight`, `test_runner_result_report`, `git_repo` setup, the C2 sweep.

## Docs, decisions, backlog
| Where | Edit |
|---|---|
| README `:977`; DESIGN-GUIDE §9 and B105 (`:1972-2062`); CONSUMERS B105 (`:59-144`), `:1426` | two trees; B105 collects `tests/` only; S1 (run `./run-gate.py tester-unified` first); the vitest file's new path |
| `CHANGES.md`; decisions (notes only) | an `[Unreleased]` entry; A-468 ((a) superseded, (c) done), A-130 and A-350 (paths) |
| backlog | B123 DONE; an S1 bullet appended after `4-backlog.md:10742` (B105 acceptance list `:10719-10760`); `:10739-10740` (A-468 amendment): `pythonpath = ["src"]` → `["src", "analysis/src"]` (CD15); plan §3 and B128: `tests/test_self_lane.py` → `gate/tests/test_self_lane.py` |
| REPORT | list (do not fix; src is forbidden) the `src/assay` comments naming moved test paths: `runner.py:1337`, `provenance.py:206-208`, `adapters/go.py:569`, `go_stmtpos.py:83`, `vocabulary.py:25,294,333`, `cli.py:575`, `config.py:918`, `adjudication.py:50`, `canary.py:190`, `verify.py:2499`, `result_reports/vitest_json.py:71` |

## Scope
| Touch | Forbid |
|---|---|
| `tests/**`, `gate/**` (only `__init__.py`, `tests/**`), the 3 `tools/` files, `assay.toml`, the pyproject comment, the docs, `nyxloom-trove/*.md` and plan, `nyxloom.toml`, `W4-*` | `src/assay/**`, `gate/{python,distribution}/**`, `run-gate.toml`, `carve-assets/**`, content edits to the dstdns SQL test (W5; W4 only moves it), `snapshot_history` (W7), `zz_slow`, `analysis/**` |

## Gate
```bash
docker ps   # no other session's gate container may be running
cd <worktree>/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > ../W4-gate.log 2>&1; echo "exit=$?"
```
Read the markers and `FAILED` lines in a **separate** step.

**No known-red exception (CD29).** The B107 test was removed by `35adca38` (in landing since `1e3c8a49`). The gate must be fully green: any `FAILED` line is BLOCKED.

**Host-load rule** (a production game server shares this host): nice/ionice serial pytest only; one gate container, none while another session's gate runs; never the full `self-qualification` lane (O10's preflight is the one allowed exception); remove containers by exact name. Editor tools for edits (`git mv` is fine). Trailer `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`

## BLOCKED rule
Write `BLOCKED: <reason>` to `W4-LOG.md`, commit and stop if: W3, W1 or W2 is unmerged; a `tests/` file fits no P1 row (added files: see P1); a helper is missing; `src/assay` behaviour would change; the P5 command shows a missing line or arc; the O10 preflight is not accepted; or the gate has any `FAILED` line. Never improvise.

## Review
A fresh-session adversarial reviewer checks git state before merge: re-derive P1; collect in both orders; forge stale receipts (a green gate then a red re-run at one commit must leave no receipt); confirm the wheel lane keeps `--override-ini=pythonpath=`.
