# Pre-dispatch adversarial review: Wave A W3 / W1 / W4 (tests + gate)

**Reviewer:** fresh session (Claude Opus 5.5), read-only. **Worktree:** `.worktrees/assay-b110-landing`, HEAD `be803c3a`. All paths are relative to `assay/` unless they start with `nyxloom-trove/`.
**Method:** AUTHORING §"Pre-dispatch adversarial handoff review" + §3b. Binding context: plan, CARVER-DECISIONS CD1–CD24, A-475..A-480, B123–B130.
**Probes run (all under `nice -n 19`, read-only):**
- recomputed W3 `expected_dir` over `git ls-tree` at both `5bbd916e` and HEAD;
- reimplemented the W3 2a extractor and `allowed()`;
- scanned `Path(__file__)` usage and `from conftest import` usage;
- `pytest tests --collect-only` at HEAD: **5965 tests, exit 0**;
- one pyflakes run;
- a scratch-dir pytest 9.1.1 probe of the W4 `gate/tests` package/conftest mechanism;
- a scratch-dir probe of `--collect-only` against a missing fixture;
- `git ls-files` pathspec probes.

Nothing in the repository was edited.

**What changed since the briefs' anchor commit `5bbd916e`:** only `tests/test_cli_run.py` (−216 lines, `35adca38`, merged at `1e3c8a49`) and `tests/fixtures/b105-coverage-exclusions.json`. W3's file counts and the import graph are unaffected. The B107 known-red test no longer exists.

---

## 1. Blocking findings

### B1. W4 pins the wrong `pythonpath` and `testpaths` (conflicts with CD15 and W2)
**Location:** W4 §P3, the `pyproject.toml:94-95 comment` row ("`pythonpath = ["src"]` is pinned"), and Work step 3 ("ini keys exactly `pythonpath=["src"]`, `testpaths=["tests"]`").
**Evidence:**
- CD15 says "The A-468(c) `pythonpath` pin becomes `["src", "analysis/src"]`, and W4's drift test pins that value."
- W2 Work 5 sets `pythonpath = ["src", "analysis/src"]` and `testpaths = ["tests", "analysis/tests"]`, and W2 merges before W4.
- If W4 is implemented as written, its drift test is red on the merged tree. A Sonnet implementer would then either "fix" `pyproject.toml` back to `["src"]`, which breaks W2's analysis imports, or BLOCK.

**Fix text (W4):**
- P3 row: "`pyproject.toml` comment above `[tool.pytest.ini_options]` (`:96-97` at be803c3a, shifted by W2): `pythonpath = ["src", "analysis/src"]` (CD15, set by W2) and `testpaths = ["tests", "analysis/tests"]` (W2) are pinned and load-bearing (A-468(c)). The override must go because `mutation_witness.py:60-63` makes any `-o` token witness-ineligible, and B110 P3b needs eligibility."
- Work 3: "`[tool.pytest.ini_options]` has exactly the keys `pythonpath` and `testpaths`, with `pythonpath == ["src", "analysis/src"]` and `testpaths == ["tests", "analysis/tests"]`. `gate/tests` is deliberately not in `testpaths`: the registered lane names it explicitly."
- O3 gains the negative "set `pythonpath = ["src"]` → red."
- Docs table gains: "`4-backlog.md:10739-10740` (the A-468 amendment inside the B105 acceptance bullet): replace `pythonpath = ["src"]` with `["src", "analysis/src"]` (CD15)."

### B2. W4's receipt does not match CD11
**Location:** W4 §P4, Receipt row and O5 ("exactly the four keys"; "extra key" is refused).
**Evidence:**
- CD11: "The gate writes `.assay/registered-gate/tester-unified.json` with commit, tree, **assay version and completion markers**."
- W4's receipt is exactly `{schema_version, lane, commit, tree}`, and O5 refuses any extra key. The brief and the binding decision contradict each other.
- The receipt is written by the host-side outer script (`tools/tester-unified-gate.sh` entry section, a `bare-host` lane). That script never sees the wheel version or the in-container phase markers: it only streams `docker logs`. Honouring CD11 therefore needs new plumbing (inner→outer), which is a design choice that cannot be left to Sonnet.

**Fix (carver chooses before dispatch):**
- **Option A (recommended): amend CD11** to: "The latest registered `tester-unified` run at this exact commit and tree exited 0. After `run_registered_tester_container` returns 0 and HEAD/tree are re-verified, the outer gate writes `.assay/registered-gate/tester-unified.json`, exactly `{"schema_version": 1, "lane": "tester-unified", "commit": C, "tree": T}`, and removes it at launch. The file exists only on the zero-exit path after every inner phase, so its existence at the matching commit and tree is the completion evidence. No version is recorded: the outer script never sees the wheel, and setuptools-scm derives the version from the commit. `b105_report_check.py --receipt-only` pre-checks it; the full check refuses a report without a matching receipt."
- **Option B:** keep CD11. Then W4 must specify:
  - how the inner run publishes version and markers (for example, `run_inner` writes `$worktree/assay/.assay/registered-gate/inner.json` as its last step);
  - the exact receipt schema with those fields;
  - the checker's comparison rules for them (does the version have to equal B105's own wheel version?);
  - O5 negatives for each new field.

### B3. W1 contradicts CD7 (ciu provenance schema 1)
**Location:** W1 Goal ("**No `src/assay` change.**"), Work 8 row `adjudication.py:106-113 (1, 2)` → "REPORT", Scope Forbid `src/assay/**`, and the BLOCKED trigger "a step needs a `src/assay` change".
**Evidence:**
- CD7 is binding and wins over the brief: "Remove schema 1 (A-477) … Note it in CHANGES as a removed legacy input."
- None of the briefs cites CARVER-DECISIONS.md, so a Sonnet implementer either never sees CD7 or hits BLOCKED.
- CD7 is not mechanical:
  - The only **real** ciu green reference, `nyxloom-trove/carve-assets/W2/ciu-provenance-green-reference.json`, is `"schema_version": 1` and frozen (A-222/CD8).
  - `tests/test_adjudication_pipeline_integration.py:92,199` feed it byte-for-byte into green-path tests. After the change, `adjudication.py:181-186` returns `(ERROR, FORMAT_MISMATCH)` and those tests go red.
  - Further schema-1 users: `test_adjudication_provenance_parse.py:32,67-70`, `test_adjudication_registry.py:50`, `test_cli_run.py:2231`, and CONSUMERS.md:3262.
  - Removing an accepted input is consumer-visible, which is an A-479 major-version question. W1 itself labels it "consumer-visible".

**Fix (carver chooses before dispatch):**
- **Option A (recommended): amend CD7** to "Deferred to the v14 wave with CD6. W1 reports it under 'For decision', as W1 Work 8 already says (A-477: 'report it for a decision'). Reason: the only real ciu green reference is schema 1 and frozen, and removing an accepted input raises A-479's version question."
- **Option B:** give W1 an exact edit map:
  - `adjudication.py:113` becomes `_ACCEPTED_SCHEMA_VERSIONS = (2,)`, and the `:105-112` comment is rewritten;
  - `_green`/`_GREEN` defaults change to 2 (`test_adjudication_provenance_parse.py:32`, `test_adjudication_registry.py:50`, `test_cli_run.py:2231`);
  - `:67-70` becomes `test_schema_version_1_is_refused_as_a_removed_legacy_shape`, asserting `(Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)`;
  - the carver specifies what replaces the real-asset green-path tests at `test_adjudication_pipeline_integration.py:92,199`;
  - CONSUMERS `:3262` and CHANGES `### Removed` are updated;
  - the Scope adds these files, and the "No src/assay change" goal and its BLOCKED trigger are removed.

### B4. W4's "added file → BLOCKED" rule fires deterministically
**Location:** W4 §P1, last paragraph: "A file added since `5bbd916e` (`git diff --diff-filter=A 5bbd916e -- tests/`), other than W3's import-contract test, is BLOCKED."
**Evidence:**
- W4 depends on W2, and W2 T5 adds the judge test `test_cli_analyze_seam.py` under `tests/` (under W3's layout it must be `tests/core/`).
- Plan §2 merges stage 2 in the order W6 → W4 → W5. W6 (P2, CD2) adds per-adapter and core guard tests, for example `test_scanner_progress_guards.py` split by component (REBASE-P0-P2:50).
- Every one of these is an `A` in that diff, so W4 BLOCKs on a correct tree.

**Fix text:** "Every file under `tests/` that is added since `5bbd916e` (`git diff -M --diff-filter=A --name-only 5bbd916e -- tests/`) is judge unless the P1 tooling table names it. List each one in `W4-REPORT.md`. Expected additions: W3 `tests/core/test_import_contracts.py`; W2 `tests/core/test_cli_analyze_seam.py`; and, if W6 merged first, W6's per-component guard tests. BLOCKED only if an added file imports `gate`/`gate.tests`, requests the `standalone` fixture, or reads `tools/`, `gate/`, a wheel or a zipapp."

### B5. The CD13 FIFO test has no owner (W4 C5 claims it, W4's scope excludes it, W2 never mentions it)
**Location:** W4 C5 ("the FIFO test, ex `test_analysis.py:721`, runs `python -m assay analyze` from source"); W4 Scope (Touch has no `analysis/**`); W2 T1 ("Keep the moved assertions unchanged", with no FIFO mention).
**Evidence:**
- `tests/test_analysis.py:721` requests `standalone` and runs `standalone.venv/bin/python -I standalone.venv/bin/assay analyze …`.
- W2 moves the file to `analysis/tests/`, which has **no conftest** (W2: "Add no `conftest.py`", "Do not import from `tests/`"). From W2 onward the analysis lane (R0 over `analysis/tests`) therefore errors with `fixture 'standalone' not found`. W2 goes red before W4 exists.
- W4 cannot fix it without touching `analysis/tests/**`, which is out of its scope and therefore BLOCKED.

**Fix text:**
- Move CD13 into **W2 T1** with exact code: `test_nonregular_inputs_refuse_without_waiting_for_a_fifo_writer(tmp_path, kind)` drops `standalone` and runs `subprocess.run([sys.executable, "-m", "assay", "analyze", *arguments], env={**os.environ, "PYTHONPATH": os.pathsep.join([str(PROJECT_ROOT / "src"), str(PROJECT_ROOT / "analysis" / "src")])}, capture_output=True, text=True, timeout=60, check=False)`.
  - `-I` is dropped because it ignores `PYTHONPATH`.
  - The timeout goes from 10 to 60, because §3b says a timeout is only a generous failsafe.
  - Assertions are unchanged.
- In W4: delete C5, or reword it to "C5: done in W2 (CD13); W4 verifies that `git grep -nw standalone -- analysis/` is empty."

---

## 2. Major findings

### M1. W1's by-name resolution recipe misses every root-level file
**Location:** W1 header, Depends on: `git ls-files 'tests/**/<name>'`.
**Evidence:**
- Probe results:
  - `git ls-files 'tests/**/test_self_lane.py'` prints **nothing**, because without `:(glob)` the pattern needs an extra `/`;
  - `git ls-files 'tests/**/test_go_r1_real.py'` finds the nested file;
  - `git ls-files ':(glob)tests/**/test_self_lane.py'` finds `tests/test_self_lane.py`.
- W1's targets `test_runner_snapshot_selection`, `test_self_lane`, `test_distribution_gate`, `test_verdict_conformance` and `test_lane_schema_v2_locked_successors` all stay at the root (W3 ROOT_PINNED). The literal recipe returns empty for each, which trips W1's BLOCKED trigger "an anchor does not re-resolve".

**Fix text:** replace the recipe with `git ls-files ':(glob)tests/**/<name>'`.

### M2. W1's O2 grep contradicts W1 Work 6
**Location:** W1 O2 command vs Work 6.
**Evidence:**
- Work 6 requires the rewritten `test_distribution_gate.py` to assert that `qualify_topos.py`, `qualify_cmru_b006a.py`, `topos-qualified` and `cmru-b006a-qualified` are **absent** from the gate script. Those literals therefore live in `tests/test_distribution_gate.py`.
- O2 greps `tests` for exactly those strings and "prints nothing" is the pass condition, so O2 fails by construction. A Sonnet implementer would obfuscate the strings (`"qualify_" + "topos"`) or BLOCK.

**Fix text:** append `':!tests/test_distribution_gate.py'` to the O2 pathspec, and add: "the only permitted hits are the absence assertions in `tests/test_distribution_gate.py`."

### M3. W3's layout test must exclude `tests/fixtures/`
**Location:** W3 2b, Tests ("every `tests/**/test_*.py` outside `qualification/` is in `tests/<expected_dir(name)>`; … no `__init__.py`, and no `conftest.py` except `tests/conftest.py`").
**Evidence:** `git ls-files` shows these fixture files:
- `tests/fixtures/canary/python/tests/test_greet.py`
- `tests/fixtures/mutation_exec/python/tests/test_checks.py`
- `tests/fixtures/canary/python/pkg/__init__.py`
- `tests/fixtures/mutation_exec/python/pkg/__init__.py`

A literal implementation is red on the real tree, which leads to BLOCKED or improvisation.

**Fix text:** "Scope every 2b check to `tests/**` minus `tests/fixtures/**` and minus `tests/qualification/**`. That covers placement, basename uniqueness, `__init__.py` and `conftest.py`."

### M4. W3's 2a extractor has no stated edge filter, and an empty walk passes
**Location:** W3 2a (`import_edges()`), and test (i).
**Evidence:**
- My reimplementation reproduces the carver's **176 edges and 0 violations** only when edges are kept for `assay`/`assay.*` targets. Unfiltered, the same walk yields **483 edges and 92 violations** (for example `assay.adapters.go -> dataclasses`), which is a deterministic false BLOCKED ("the tree violates 2a").
- Test (i) also passes if the walker scans a wrong or empty directory. `PROJECT_ROOT` from a `tests/core/` file is a new failure mode here, and 0 edges means 0 violations.

**Fix text** (after "drops self-edges"): "Keep an edge only when the resolved imported name is exactly `assay` or starts with `assay.`. Stdlib, third-party and `assay_analysis` imports are not edges. Test (i) also asserts:
- the walked module set is non-empty and equals the module names of `sorted((PROJECT_ROOT / "src" / "assay").rglob("*.py"))`;
- the edge set contains `("assay.cli", "assay.adapters.python")`, `("assay.coverage", "assay.coverage_parsers.lcov")` and `("assay.mutation_parsers", "assay.errors")`. The last one is a function-level relative import inside an `__init__.py`.

Do not assert the edge count."

### M5. W3's O3 and O5 set-equality oracles ignore the new test file, so they always differ
**Location:** W3 Work 2 (collect-before is taken before step 3 adds `test_import_contracts.py`); O3 ("same `(basename, test id)` set"); O5 ("same keys and outcomes as `before.xml`"); BLOCKED ("O3 or O5 differs").
**Evidence:** after step 3 the after-sets contain the new contract-test ids, so both oracles differ on every correct implementation.
**Fix text:**
- O3: "the after-set equals the before-set plus exactly the ids from `test_import_contracts.py`. Nothing is removed or renamed apart from its path. At be803c3a the before-set has 5965 ids."
- O5: "same keys and outcomes after excluding `test_import_contracts.py`."
- O3 negative made deterministic: "revert 2c in `tests/core/test_isolation.py:58` (`FIXTURES` is read at import time by `ROOT_MANIFEST`/`TREE_GRAMMAR`) → collect-only errors with `FileNotFoundError`." Most other files read their fixture paths only inside test bodies, so collect-only stays green for them and "see red" is impossible.

### M6. W3's O6 pyflakes command can never be clean
**Location:** W3 O6: `nice -n 19 python3 -m pyflakes src/assay tests`.
**Evidence:**
- The probe exits 1 on `tests/fixtures/mutation/python/broken.py:8:12: invalid syntax`, a deliberately broken fixture.
- The gate excludes `tests/fixtures` by design (`tester-unified-gate.sh:117-125`).
- With the exclusion below, the tree is pyflakes-clean today (exit 0).

**Fix text:** `find tests -path tests/fixtures -prune -o -type f -name '*.py' -print0 | xargs -0 nice -n 19 python3 -m pyflakes src/assay`

### M7. W3 Part 1's "contexts within ±1% of the junit test count" check is wrong
**Location:** W3 Part 1, Deliverable checks.
**Evidence:**
- pytest-cov 7.1 records contexts as `f'{item.nodeid}|{when}'` with `when` ∈ {setup, run, teardown} (`pytest_cov/plugin.py:439-450`). Only (test, phase) pairs that executed a measured line appear.
- The measured-context count is therefore neither ≈ the test count (it can reach 3×) nor bounded below by it: static AST tests execute no src line.
- The ±1% rule would STOP on a correct run.

**Fix text:** "Normalise each context to its nodeid (strip the `|setup`/`|run`/`|teardown` suffix). Record:
- the number of distinct nodeids;
- the junit testcase count;
- the size of the empty context `""`.

Stop only if no non-empty context exists. That is the sysmon/wrong-core symptom: every line lands in `""`."

### M8. W4's path rule rewrites the vitest fixture path wrongly, and the break is silent
**Location:** W4 P2, the "moved modules" row ("`Path(__file__)…parents[1]` → `PROJECT_ROOT`").
**Evidence:**
- `tests/qualification/test_javascript_real_vitest.py:44-46` has `_PROBE_JS = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "probe-js"`.
- There `parents[1]` is `tests/`, not the project root. The rule produces `PROJECT_ROOT / "fixtures" / …`, which does not exist.
- The test is opt-in (`ASSAY_NODE_QUALIFICATION=1`) and skips in every gate, so the break is silent. `test_go_r1_real.py:54` (`parents[2]` → `parents[3]`) has the same silent-failure profile.

**Fix text:**
- "`gate/tests/qualification/test_javascript_real_vitest.py`: `_PROBE_JS = PROJECT_ROOT / "tests" / "fixtures" / "coverage" / "probe-js"`, with `PROJECT_ROOT` from `gate.tests.support`."
- "`test_go_r1_real.py`: `_PROJECT_ROOT = PROJECT_ROOT` (import it from support) instead of `parents[3]`."
- Add oracle **O9** in a gate test, for example `gate/tests/test_self_lane.py`: `(gate.tests.qualification.test_javascript_real_vitest._PROBE_JS / "package.json").is_file()` and `(gate.tests.qualification.test_go_r1_real._PROJECT_ROOT / "pyproject.toml").is_file()`. Negative: restore `parents[1]` → red.

### M9. W4 never updates `test_self_hosting.py`'s hand-transcribed argv pin
**Location:** W4 P3 (tester-unified argv change) vs `tests/test_self_hosting.py:80-88` (`DECLARED_LANE_ARGV`) and `:171` (`assert document["argv_effective"] == DECLARED_LANE_ARGV`).
**Evidence:**
- The pin is "transcribed BY HAND". It only runs in the independent witness inside the registered gate; locally the test needs `ASSAY_SELF_HOSTING_VERDICT`.
- Result: a correct W4 goes red only at the gate, and a Sonnet implementer hits "gate red beyond the known test" → BLOCKED.

**Fix text** (P3 table, new row): "`gate/tests/test_self_hosting.py` `DECLARED_LANE_ARGV` = exactly `["python","-m","pytest","tests","gate/tests","-q","--ignore=gate/tests/test_self_hosting.py","--override-ini=pythonpath="]`."

### M10. W4's O2 negative cannot fire: `--collect-only` does not resolve missing fixtures
**Location:** W4 O2 (the observable is "`pytest tests --collect-only` needs nothing from `gate/`"; the break is "a judge test that requests `standalone` → error").
**Evidence:** Probe: a file with `def test_x(standalone): pass` gives `--collect-only` "1 test collected", exit 0, and gives `--setup-only` "ERROR". A wrong implementation that leaves a judge test requesting `standalone` passes O2.
**Fix text:** "O2: a committed judge test in `tests/core/test_import_contracts.py` (section 3, 'judge/tooling separation') AST-scans every `tests/**/test_*.py` outside `tests/fixtures/`. It fails on:
- any `import gate…` / `from gate…`;
- any test-function parameter named `standalone`;
- any `from conftest import` of `Standalone`, `_clean_env`, `_build_backend_home` or `requires_parent_repository`.

Negative: plant `def test_x(standalone): assert standalone` → red, naming the file."

### M11. W4 removes tests from B105 collection without proving R1
**Location:** W4 O8 ("The B105 lanes are W7's to prove"); P5 ("a missing arc gets a new stand-in in the second file").
**Evidence:**
- W4 removes 171 collected tests from B105's suite. Per-file counts at HEAD:
  - `distribution_build_release` 33, `distribution_gate` 26, `distribution_release_wheel` 24;
  - `standalone` 21, `dependency_purity` 14, `self_lane` 13;
  - `b105_report_check` 11, `qualification` 9, `cgroup_parent` 6;
  - `go_helper_is_packaged` 6, and 6 of the 8 in `verdict_schema_is_packaged`.
- S1 keeps their R0 assurance. Their in-process coverage and mutant kills leave B105, however. P5's three regions are reasoned, not measured (§3b-F).
- Nothing in W4 runs the preflight, so an R1 < 100% regression merges and surfaces only at W7.
- W7's drift proof needs a full-history preflight **on the post-split suite** as its baseline. No package produces one.
- "A missing arc gets a new stand-in" leaves test design to Sonnet.

**Fix text:**
- Add **O10**: "After the W4 gate, run `nice -n 19 ionice -c3 python ./run-gate.py self-qualification-preflight` once (plan §4 allows it; CD9: no receipt needed). Required result: checker `B105_REPORT_ACCEPTED`, R1 PASS. Record collected, passed and skipped counts in `W4-REPORT.md` as W7's full-history baseline."
- Replace "a missing arc gets a new stand-in" with "a missing line or arc is BLOCKED (the carver specifies the stand-in)", unless the carver runs the P5 command before dispatch and records the result.
- Give the exact P5 command: `PYTHONPATH=src nice -n 19 python3 -m pytest tests/core/test_cli_provenance_and_request_base.py tests/core/test_b105_provenance_boundaries.py -q -p no:cacheprovider --cov=assay.provenance --cov-branch --cov-report=term-missing` (whole files; pytest cannot select a line range).

### M12. W4 C2's "Cache them (R9 RC8)" is an unspecified design task
**Location:** W4 C2.
**Evidence:**
- R9 RC8 means "session-cached parse" of `src`. No Work step, file, mechanism or oracle covers it.
- Per the operator rule, no design choice may be left to Sonnet.

**Fix text:** delete "Cache them (R9 RC8)" and file it as a follow-up under B123 or A-468(b).

### M13. The obsolete B107 known-red note is still in all three briefs
**Location:** W3 Gate ("…(now `tests/core/test_cli_run.py`) fails on main (B107)…"); W1 Gate; W4 Gate; W3 O7.
**Evidence:**
- `git grep thread_join_hang` finds nothing: `35adca38` removed the test and `1e3c8a49` merged it into landing.
- W3 even tells the implementer that the test moved.

**Fix text:** replace each known-red paragraph with: "**No known-red exception.** The B107 test was removed by `35adca38` (in landing since `1e3c8a49`). The gate must be fully green: any `FAILED` line is BLOCKED." In W3 O7, replace "green except the known red test" with "green."

### M14. W3's ROOT_PINNED decays: 7 judge files have no owner after Wave A, and the pin list goes stale
**Location:** W3 2b `ROOT_PINNED` (and CD23: "stay at the root until those packages move or remove them"); W1; W4.
**Evidence (fate of all 24 root-pinned files):**

| Fate | Files |
|---|---|
| Deleted by W1 (3) | `test_python_qualification`, `test_gate_qualify_cmru_b006a`, `test_gate_harness_version_pins` |
| Moved out of `tests/` by W2 (2) | `test_analysis`, `test_analysis_json_framer` |
| Moved out of `tests/` by W4 (11) | `test_self_hosting`, `test_distribution_build_release`, `test_distribution_gate`, `test_distribution_release_wheel`, `test_standalone`, `test_cgroup_parent`, `test_self_lane`, `test_go_helper_is_packaged`, `test_dependency_purity`, `test_b105_report_check`, `test_gate_failure_diagnostics` |
| Moved out of `tests/` by W5 (1) | `test_gate_qualify_dstdns_sql` |
| **Left at root, no owner (7)** | `test_runner_snapshot_selection`, `test_lane_schema_v2_locked_successors`, `test_verdict_v13_successors` and `test_b106_reuse_and_witness` (their path references go away with W1's gate/assay.toml deletions); `test_verdict_conformance` and `test_errors` (pinned only by the never-executed `carve-assets/P23` and a `verify.py` docstring); `test_verdict_schema_is_packaged` (kept half after W4's split) |

The layout test also allows "pinned names may be absent", so all 17 removed names stay allow-listed forever.

**Fix (carver decides; exact text to insert):**
- Each package deletes its own names from `ROOT_PINNED` in the same commit: W1 its 3 names; W2 its 2; W4 its 11; W5 `test_gate_qualify_dstdns_sql.py`.
- **W1 adds a step:** "`git mv` `test_runner_snapshot_selection.py`, `test_lane_schema_v2_locked_successors.py` and `test_verdict_v13_successors.py` to `tests/core/`. Apply 2c (`test_verdict_v13_successors.py:19` `Path(__file__).resolve().parents[1]` → `PROJECT_ROOT`). Remove their names from `ROOT_PINNED`."
- **W4 adds a step:** "`git mv` the kept half of `test_verdict_schema_is_packaged.py` to `tests/core/` and remove its name."
- Keep `test_b106_reuse_and_witness.py` (whose generated-code string literals contain `Path(__file__)` at `:668,745,860,872`), `test_verdict_conformance.py` and `test_errors.py` pinned, and rewrite the `ROOT_PINNED` comment to give the true reason for each.

### M15. A-477's "any non-current version is refused" is only half-proved for 2–3 schemas
**Location:** W1 Work 7, rows marked "exists" (shard summary, mutation-state record, resource snapshot).
**Evidence:**
- `test_b105_mutation_boundaries.py:77` tests only `schema_version=999`.
- `test_liveness_resources.py:314` tests only `2` against the current value.
- A `!=` → `>` mutant at `mutation.py:1801` or `liveness_resources.py:179-180` survives, which is exactly the break W1 uses as O3 for the verdict and lane schemas.
- So W1's "one refusal test per assay-owned schema" is hollow for these rows.

**Fix text (Work 7):**
- "Shard summary: add `([_shard(schema_version=mutation.MUTATION_STATE_SCHEMA_VERSION - 1)], "unsupported shard schema_version")` and the same with `0` to the parametrization at `test_b105_mutation_boundaries.py:73-83`."
- "Resource snapshot: add `assert compare_resource_snapshots(current, {**current, "schema_version": RESOURCE_SNAPSHOT_SCHEMA_VERSION - 1}) == ("unknown", {})` to `test_compare_requires_a_pair_of_complete_same_identity_snapshots`."
- "State record: confirm that `test_resume_reruns_a_state_record_after_a_routine_schema_version_bump` covers a lower version, else add one." (The carver checks this before dispatch.)
- O3 gains: "`!=` → `>` at `mutation.py:1801` → the new `-1` case fails."
- Scope gains `test_b105_mutation_boundaries.py`, `test_liveness_resources.py` and `test_mutation_progress_budget_plan.py`.

### M16. W4's O7 cannot observe "no receipt when the container failed"
**Location:** W4 P4 (Entry section: "Capture C and T, then clear → `run_registered_tester_container` → finish") and O7 ("order clear<run<finish pinned").
**Evidence:**
- `gate_functions` (`test_distribution_gate.py:52-78`) sources only the text **above** `# --- entry points`, so the entry-section sequence cannot be executed under `run_bash`. The order is pinned only textually.
- Wrong implementation that passes: `run_registered_tester_container … || true` followed by an unconditional `finish`. The textual order still holds, and a red container still yields a receipt and COMPLETE.

**Fix text:**
- "Add a fourth function above the marker, `run_registered_gate W HOST CG`: capture C and T; clear; `run_registered_tester_container "$W" "$HOST" "$CG"`; `finish_registered_gate "$W" "$C" "$T"`. The entry section's last line is `run_registered_gate "$worktree" "$host_repo_root" "$cgroup_parent"`."
- O7 adds: "with `run_registered_tester_container() { return 7; }` stubbed after sourcing, `run_registered_gate` exits non-zero and leaves no receipt and no COMPLETE line."
- Break: "`|| true` after the run call → red."

---

## 3. Minor findings (exact fix text inline)

- **m1. Stale anchors.** None of these changes the meaning, but a Sonnet implementer must not have to guess.
  - W3 `4-backlog.md:11670` → **11673**.
  - W1 `4-backlog.md:10726` → the tag-audit bullet is **10729-10742**.
  - W4 "S1 bullet in B105 acceptance (`:10716`)" → the acceptance list is **10719-10760**; append after `:10742`.
  - W4 `pyproject.toml:94-95/94-97` → the comment is at **96-97** and the ini at **98-100**.
  - W4 `tests/conftest.py:1389-1497` and "`standalone` … `1431-1497`" → both end at **1499**.
  - W4 "Heavy judge tests": `test_cli_run.py:463/586` were **deleted** by `35adca38`, and `:677`/`:1720` shifted. Use test names.
  - W3/W1/W4 "from `assay-b110-landing` (`5bbd916e`)" → "from the current tip (≥ `be803c3a`)".
- **m2. W1 Work 2.** Also rewrite the P26 comment at `tester-unified-gate.sh:406-419`: its "SHAPE coverage moves into P33's own suite (`test_p26_attestation_shapes_survive_v5`…)" describes a suite W1 retires. Text: "After A-477 the three template-coupled P26 nodes stay deselected; their historical successors are retired and not executed."
- **m3. W1 Docs, §15 row.** "Keep … the `…-b006a` anchor" points nowhere near §15: that anchor is §6 `:1743` (`#snapshot-selection-…-b006a`, linked from README:122 and CONSUMERS:2925). Reword to "do not touch §6's `…-b006a` heading at `:1743`; nothing links to §13/§15 anchors."
- **m4. W1 Work 3 and Docs.** "No collected test reads history or tags since A-475" is false between W1 and W4: `test_distribution_build_release.py` is still collected in B105, and setuptools-scm `describe` walks tags (W1 Work 13 says so itself). Text: "no collected **judge** test reads history or tags (A-475); `test_distribution_build_release.py` leaves B105 collection with W4."
- **m5. W1 O1 and O4 bypasses.**
  - O1: the absence check must test the bare substrings (`topos-qualified`, …, `verdict-v13-successors-verified`) anywhere in the script, not only the `echo '…'` spelling. This works because the rewritten comments no longer name them.
  - O4: `test_self_lane` must also assert `not any(a == "--deselect" or a.startswith("--deselect") for a in lane.argv)` for **both** B105 lanes. A two-token `--deselect nodeid` passes the prefix-set check.
- **m6. W1 Work 10.** "List any further row you annotate in the REPORT" leaves a judgment to the implementer. Text: "Annotate only the listed rows. Any other decision row that the `git grep` hits is listed in the REPORT under 'For decision' and not annotated."
- **m7. W1 Work 13.** Add a BLOCKED trigger: "a test that stays in `tests/` after W4 runs git on the real repository beyond `HEAD`, or reads outside `assay/`."
- **m8. W4 P4 quoting.** The COMPLETE echo must be literally `echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'`, because W1's Work 6 test asserts that exact string.
- **m9. W4 P4 receipt bytes and mode.**
  - Bytes: `printf '{"schema_version": 1, "lane": "tester-unified", "commit": "%s", "tree": "%s"}\n' "$C" "$T" > "$tmp"`.
  - Before writing: `mkdir -p "$W/assay/.assay/registered-gate"`.
  - Before `mv -f`: `chmod 0644 "$tmp"`. `mktemp` creates 0600, and the B105 driver reads the file inside the tester-unified container.
- **m10. W4 checker CLI glue.**
  - `--receipt-only` form: `--receipt-only --tester-unified-receipt P --expected-commit C --expected-tree T`.
  - Drop `required=True` from the nine existing flags. In full mode, `parser.error` names each missing flag; in receipt-only mode, `parser.error` if any other flag is set.
  - Success line on stdout; refusals on stderr, as today.
  - Driver: insert immediately after `self-qualification-gate.sh:60`: `if [[ "$requested_lane" == self-qualification ]]; then echo "B105_PHASE=require-same-commit-tester-unified-pass"; "$tester_python" "$scratch/source/assay/tools/b105_report_check.py" --receipt-only --tester-unified-receipt "$receipt" --expected-commit "$source_commit" --expected-tree "$source_tree" || die "…"; fi`.
- **m11. W4 lint row.** "add a second array" becomes "add one more array" (W2 already added the `analysis` tree).
- **m12. W4 new-test placement.** O5 and O6 go in `gate/tests/test_b105_report_check.py`; O7 goes in `gate/tests/test_distribution_gate.py`; the O2 AST check goes in `tests/core/test_import_contracts.py` (M10).
- **m13. W4 split, kept half.** Its import line becomes `from conftest import PROJECT_ROOT, SCHEMA_PATH`, with no `Standalone`, so the conftest-deletion rule is not blocked by a stale import.
- **m14. W4 P2 conftest deletion rule.** Make it deterministic: "After W1/W2, `git grep -nwE '_parent_repository_toplevel|_PARENT_TOPLEVEL|requires_parent_repository|_build_backend_home|_clean_env|Standalone|standalone' -- tests/ ':!tests/conftest.py'` must be empty. Then delete all of them from `tests/conftest.py` (support.py holds the moved copies). A non-empty result is BLOCKED."
- **m15. W4 P2 contradiction.** "`pytest_sessionfinish` appears in no `gate/tests` file" contradicts support.py's `b105_coverage_sessionfinish = judge.pytest_sessionfinish`. Reword: "no `gate/tests` module binds a top-level name `pytest_sessionfinish`."
- **m16. W4 "Keep W2's tokens" (P3 B105 row).** W2 adds no argv tokens to the B105 lanes. Delete the phrase; the argv given is exact.
- **m17. W4 C4 / CD12 follow-through.**
  - Docs table adds: plan §3 and B128 (`4-backlog.md`) paths `tests/test_self_lane.py` → `gate/tests/test_self_lane.py`.
  - Work 3 must keep the `snapshot_history == "full"` pin for W7 to flip.
  - `nyxloom.toml` is in Touch without a step. Add: "`nyxloom.toml:125-137` prose path → `gate/tests/test_self_hosting.py`; `:148-149` 'four inner phase markers' → the actual count."
- **m18. W4 REPORT list.** List the unfixable stale `src` comment paths (src is forbidden):
  - `runner.py:1337`, `provenance.py:206-208`, `adapters/go.py:569`, `go_stmtpos.py:83`, `vocabulary.py:25` (W4 moves);
  - `cli.py:575`, `config.py:918`, `adjudication.py:50`, `canary.py:190`, `vocabulary.py:294,333`, `verify.py:2499`, `result_reports/vitest_json.py:71` (W3 moves).
- **m19. W3 details.**
  - 2a(iii): name the synthetic importer. For example: importer `assay.coverage_parsers.fake` (not a package) with source `from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from assay.runner import X\ndef f():\n    from ..adapters import python\n` gives edges exactly `{("assay.coverage_parsers.fake","assay.runner"),("assay.coverage_parsers.fake","assay.adapters.python")}`. Plus an `__init__` case: importer `assay.adapters` (package) with `from .base import LanguageAdapter` gives `("assay.adapters","assay.adapters.base")`.
  - 2b: give the synthetic-name table literally, for example:
    - `test_adapters_go_x.py`→`adapters/go`
    - `test_coverage_lcov_x.py`→`parsers/coverage`
    - `test_mutation_format_registry.py`→`parsers/mutation`
    - `test_result_reports_x.py`→`parsers/result_reports`
    - `test_runner_sql_x.py`→`adapters/sql`
    - `test_evaluate_javascript_end_to_end.py`→`parsers/coverage`
    - `test_self_lane.py`→`""`
    - `test_cli_run.py`→`core`
  - O1's break edits `src/assay/runner.py`, which Scope forbids. Add "(temporary, never committed; allowed)".
  - Context paths such as `reports/b110/…`, `scripts/r11/candidates.py`, `decisions.md` and `4-backlog.md` sit under `nyxloom-trove/`, not under `assay/` as the header claims. For example, give `nyxloom-trove/reports/b110/research/scripts/r11/candidates.py`.
  - §11 placement: "append as the last `###` of §11, before `## 12.` at `:2967`."
- **m20. Cross-brief W2 items** found while checking W3/W4 consistency.
  - W2 T5's new judge test must be `tests/core/test_cli_analyze_seam.py` (W3 layout).
  - W2 T6's "or else in `test_dependency_purity.py`" must go: that file leaves `tests/` with W4, while CD10 makes import contracts judge. T6 goes only in `tests/core/test_import_contracts.py`.
  - W2 should delete W3's `analysis` row, `ANALYSIS_DEPS` and the `assay.verdict->assay.analysis` refusal (dead after W2), or state that they stay.
  - `test_dependency_purity`'s AST half is itself a "static source rule" (CD10's principle) yet moves to `gate/tests`. The carver should confirm this is intended.
- **m21. W3 Part 1 step 4.** `COVERAGE_FILE=$SCR/before.coverage` is inherited by any child that runs coverage with the ambient environment. Note it as a measurement caveat. Also, `test_analysis.py`'s FIFO case builds the `standalone` wheel despite the "no wheel builds" intent of the ignore list; record it as a gap.

---

## 4. Cross-brief checks (the questions asked)

| Question | Answer |
|---|---|
| W3 paths vs W1 deletes / W4 moves | Consistent: every W1-deleted and W4-moved file is root-pinned, so it stays at `tests/` for them. W1's recipe fails for root files (**M1**). W1's `test_config_reject` edit resolves at `tests/core/` by name. |
| 24 root files vs what W1/W2/W4/W5 touch | 17 leave via W1/W2/W4/W5; **7 stay with no owner** and the pin list goes stale (**M14**). W3 counts verified: 221 move / 24 stay; 156/24/15/12/5/5/2/2 by directory; 27 files, 30 occurrences, exactly the 4 forms, all real code (no string literals). |
| Gate-script collisions W1 vs W4 | No textual overlap. W1 edits `run_inner` 406-701; W4 edits `:134-152`, `:367` and `:791-821` and adds functions above the marker. One semantic coupling: the COMPLETE echo literal (**m8**). W2 and W4 both edit `run_lint_phase` (**m11**); W5 later inserts an outer phase between run and finish, which is compatible with the clear<run<finish order. |
| W4 pythonpath pin vs CD15 | **No** (**B1**). |
| CD5 in W1 | **Yes**: Work 4 deletes both tests, Work 3 removes all 4 deselect lines, Work 5 pins the empty set (tighten per **m5**). |
| W4 receipt vs CD11 | **No** (**B2**). |
| `test_self_lane.py` per CD12, W7 referenced | It moves (P1 table, C4), and W7 is referenced in C4/O8. Plan §3 and B128 are not updated (**m17**). |
| B105 claim after W1+W4 | R0 assurance for the 171 moved tooling tests is bound through S1, and W1's removals are retired by A-475/A-477. W1 deletes nothing B105 measured: the Topos tests skip in snapshots, and the CMRU tests run assay only in subprocesses. **R1/R2 evidence contributed by the moved tests is not re-proved** (**M11**). That loss is visible rather than silent, but it is deferred. The retired P33 (13 live nodes) and W9 (3) suites were never part of B105. |
| W4 runs `gate/tests` in the registered gate | Yes: the tester-unified argv adds `gate/tests`, the witness path moves, and lint covers the tree. But the witness's argv pin breaks (**M9**), and the opt-in qualification paths break silently (**M8**). |
| W3 leaves no test uncollected | The mechanics are verified:<br>• pytest recursion covers the new directories;<br>• sibling imports (`test_verify_ingested_r2`→`test_runner_ingested_r2`, both core; the EXCEPTIONS pair; conformance→errors, both root) resolve under prepend mode;<br>• `collect_ignore_glob` is unaffected.<br>The node-count oracle is 5965 at be803c3a → 5965 + (ids in `test_import_contracts.py`), set-equal otherwise (**M5**). Directories are now collected before root files, and that order change is caught by O5. |
| Host/gate rules | Pasted and correct in all three: nice/ionice, one gate container, `docker ps` first, never full self-qualification, exact-name removal, markers read in a separate step. `../WX-gate.log` sits at the worktree root and is ignored by `.gitignore:115` (`*.log`), so it is not DIRTY_TREE. The B107 note is obsolete in all three (**M13**). |

---

## 5. Oracle attacks: wrong implementations that still pass, and the fix

| Brief/oracle | Plausible wrong implementation that passes | Closing fix |
|---|---|---|
| W3 O1 (contract test) | The walker points at an empty or wrong root, so 0 edges and 0 violations pass (i) | **M4** (non-empty module set plus known edges) |
| W3 O1 | No edge filter, so the implementer "fixes" the dict | **M4** |
| W3 O2 (layout) | The scan skips `tests/fixtures`, but nobody said so, so it is red on the real tree | **M3** |
| W3 O3 | The negative picked a file whose paths are read only in test bodies, so collect-only stays green | **M5** (use `test_isolation.py:58`) |
| W1 O1 | The absence check matches only `echo '…'`, so a re-added marker with `"` passes | **m5** |
| W1 O3 | The parametrized test builds its expected message from a `verify` helper (tautology) | Require the literal f-string in the test, as at `test_verdict_conformance.py:1320-1324` |
| W1 O4 | A two-token `--deselect nodeid` passes the prefix set | **m5** |
| W1 Work 7 "exists" rows | A `!=`→`>` mutant at `mutation.py:1801` / `liveness_resources.py:179` survives | **M15** |
| W4 O2 | A judge test still requests `standalone`, and collect-only is green | **M10** |
| W4 O5 | The commit is compared case-insensitively or by prefix | Add negatives: uppercase hex of the right commit; the right commit plus 24 extra hex digits |
| W4 O6 | — (sound once the checker requires the flag for `self-qualification`) | — |
| W4 O7 | `run_… \|\| true; finish …` passes the textual order | **M16** |
| W4 O8 | R1 < 100% after the split, not run | **M11** (preflight O10) |
| W4 opt-in qualification moves | Wrong fixture root, skipped in the gate | **M8** (O9) |

**Combined-axis fixtures likely to break a convenient implementation** (AUTHORING asks for at least three):
1. **Wheel-only import plus package conftest:** run `gate/tests/test_self_hosting.py` alone with `/opt/tester-venv/bin/python -m pytest … --override-ini=pythonpath=` and `PYTHONPATH=run-venv-site`. It must import `gate.tests.support`, load `tests/conftest.py` as `assay_judge_conftest`, and import `assay` from the wheel. My scratch probe of the mechanism passed in both collection orders and for single-file collection.
2. **Stale receipt plus red re-run:** a green gate at C writes a receipt; a second gate at C goes red. The receipt must be gone, so B105 full at C exits 2. This exercises clear-at-launch.
3. **Merged W2+W6+W3 tree plus W4 rules:** W4's added-file rule (**B4**), layout placement of W2's and W6's new tests, and `pythonpath` drift (**B1**) all at once.
4. **W1 absence list plus W1 O2 grep** in the same tree (**M2**).
5. **Opt-in toolchain present:** `ASSAY_NODE_QUALIFICATION=1` in the devcontainer after W4. The only moment the wrong `_PROBE_JS` shows (**M8**).

---

## 6. Anchor spot-check (47 checked at be803c3a; ✓ = exact or trivially adjacent)

| Brief | Anchor → result |
|---|---|
| W3 | `4-backlog.md:11670` → 11673 ✗(m1) · `decisions.md:1023` ✓ · `cli.py:72-89` (adapter imports 86-89) ✓ · `cli.py:134` ✓ · `cli.py:516` `_built_in_registry` ✓ · `__init__.py:19-31` ✓ · `coverage.py:58-66` ✓ · `mutation.py:149-155` ✓ · `conftest.py:52` ✓ · `test_javascript_real_vitest.py:37` ✓ · `conftest.py:1220-1223` ✓ · `DESIGN-GUIDE.md:712` ✓ · `CONSUMERS.md:1746,2111` ✓ · README `## Testing` :1024 ✓ · `test_verdict_conformance.py:296` ✓ · 51 modules ✓ · 176 edges / 0 violations ✓ · 221/24 and per-directory counts ✓ · 27/30/4 forms ✓ · RUNTIME-ANALYSIS §7.10 ✓ |
| W1 | `decisions.md:1020,1022` ✓ · `run_inner` 372-711 ✓ · blocks 456-524 / 526-536 / 538-627 / 629-636 / 638-655 / 659-679 / 681-701 ✓ · comment 424-431 ✓ · P26 441-454 ✓ · 4× `/opt/tester-venv/bin/python` ✓ · `test_distribution_gate.py:226-254`, `256-268` ✓ · `test_self_lane.py:116-127` ✓ · `test_runner_snapshot_selection.py:51-59`, `:774`–EOF(919) ✓ (exactly `REPO_ROOT` and `requires_parent_repository` become unused; probed) · `assay.toml:56-60,71-77,85-86,193-194` ✓ · `verify.py:3028-3042` ✓ · `config.py:1468-1485` (`!=` at 1480) ✓ · `test_verdict_conformance.py:1305,1327-1342` ✓ · `test_config_reject.py:121-126` ✓ · `mutation.py:1801-1805` ✓ · `liveness_resources.py:179-180` ✓ · `reuse.py:14,57-69` ✓ · `runner.py:6040-6045` ✓ · `cli.py:1870` ✓ · `adjudication.py:106-113` ✓ · `provenance.py:177-179` ✓ · `nyxloom.toml:160-177` ✓ · README 992-993 ✓ · DG 1988-1992 ✓ · CONSUMERS 67-70 ✓ · DG §13 (3107) ✓ · DG §15 3268-3327 ✓ · `:763` cites §16 ✓ · `…-b006a` anchor ✗(m3) · `4-backlog.md:10726` ✗(m1) · `test_gate_qualify_dstdns_sql.py:60` ✓ · `test_self_hosting.py:122` ✓ |
| W4 | `decisions.md:1021/968/393` ✓ · gate `:134-152,364-370,367,791-821,821` ✓ · `self-qualification-gate.sh:24-67,156-214` ✓ · `assay.toml:17-44,30-43,65-90,185-198` ✓ · `pyproject.toml:94-97` ✗(m1) · `mutation_witness.py:60-63` ✓ · `conftest.py:276-335,501-565,1356-1370` ✓ · `:1389-1497`/`1431-1497` ✗(m1, 1499) · `test_verdict_schema_is_packaged.py` 64/134/163/171/189/209/240/258 ✓ · `test_analysis.py:721` ✓ · `test_go_r1_real.py:54` ✓ · `provenance.py` 104-118/120-191/193-320 ✓ · `go_stmtpos.py` 124-171 ✓ · `__init__.py:42-45` ✓ · `test_b105_source_coverage_controls.py:38` ✓ · `.gitignore:343` ✓ · family counts (225, all 17 families) ✓ · `test_cli_run.py:463/586/677/1720` ✗(m1) · `4-backlog.md:10716` ✗(m1) · CONSUMERS `:1426` ✓ |

---

## 7. Verdicts

| Brief | Verdict | Must fix before dispatch |
|---|---|---|
| **W3** (component boundaries) | **READY-WITH-FIXES** | M3, M4, M5, M6, M7, M13; M14's `ROOT_PINNED` comment and ownership (with W1/W4); minor m1, m19. No design choice remains once these are applied. The counts, graph and move mechanics are verified. |
| **W1** (retire) | **NOT READY** | **B3** (CD7 contradiction; the carver picks withdraw or edit map); M1, M2, M13, M14 (W1 part), M15; minor m1–m7. With CD7 withdrawn (Option A) it becomes READY-WITH-FIXES. |
| **W4** (test split, S1) | **NOT READY** | **B1** (CD15 pin), **B2** (CD11 receipt), **B4** (added-file BLOCKED rule), **B5** (FIFO ownership, fixed in W2); M8, M9, M10, M11, M12, M13, M14 (W4 part), M16; minor m8–m18. |

**General:** none of the three briefs cites `CARVER-DECISIONS.md`, and each still lists its C/Q questions as open "Rec.:" items. Mark each one "Decided (CDn)" in the brief text, so a Sonnet implementer never has to reconcile two documents.
