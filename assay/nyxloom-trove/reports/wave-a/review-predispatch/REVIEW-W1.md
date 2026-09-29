# REVIEW-W1: Wave A W1 (B124 + B125), retire cross-project and historical-schema checks

Reviewer: fresh adversarial session, 2026-09-29. Read-only: nothing in any worktree was edited or committed.
Subject: `git diff 762b500d..wave-a-w1-retire` (worktree `/workspaces/vbpub/.worktrees/wave-a-w1-retire`). The commits are `bb696f53` (code) and `fb4b0cc7` (docs and log).
Binding inputs: `W1-retire.md` and `CARVER-DECISIONS.md` (CD5, CD6, CD7 amended, CD8, CD29, CD43, CD44).
The registered gate was not run (CD44).

**Verdict: MERGE-WITH-FIXES.** BLOCKER 0, MAJOR 1, MINOR 6.

---

## Findings

### W1R-1 (MAJOR): retiring the W9 phase dropped the only freeze of the current verdict schema's bytes

- **Where:** `tools/tester-unified-gate.sh`, the retired `verdict-v13-p25-successors-verified` phase. It ran `nyxloom-trove/carve-assets/W9/test_acceptance_v13.py::test_shipped_schema_is_byte_identical_to_the_locked_v13_asset` (`W9/test_acceptance_v13.py:26-29`).
- **Evidence:**
  - That test compared the **current** `src/assay/schemas/verdict.schema.json` with a locked copy of the **current** v13 schema. It checks current behaviour, not a historical one: under A-170 exactly one schema is active, and this test stopped anyone editing it without bumping the version.
  - No remaining test pins the schema content:
    - `git grep` finds no byte, digest or content comparison of `verdict.schema.json` in `tests/`.
    - The only identity pins left are `$id == urn:assay:schema:verdict:13` (`tests/test_verdict_schema_is_packaged.py:275`) and the schema's own `const: 13`.
  - **Concrete failure (the combined-axis attack):**
    1. I copied `src/` to scratch and added one optional top-level property, `operator_note`, to `verdict.schema.json`. I left `$id` and `const` at 13.
    2. I then ran the 22 test modules that load or validate the schema (`load_schema`, `Draft202012Validator`, `why_invalid`, the `SCHEMA_PATH` users) against that copy with `--override-ini=pythonpath=<scratch>/src`. The result was **1315 passed, 0 failed**.
    3. The new parametrized refusal test cannot catch this, because it derives every expectation from `VERDICT_SCHEMA_VERSION`.
    4. Before W1, the W9 gate phase went red on exactly this edit. After W1, an unversioned change to the v13 contract ships green.
  - The digests match today. Both `src/assay/schemas/verdict.schema.json` and `carve-assets/W9/verdict.schema.v13.json` hash to `ade21cf0313d798b6b0ba9e41871bdcb310a33101c1b66317d1dbfc569e68734`.
- **Fix:** in `tests/test_verdict_conformance.py`, insert the block below immediately after the end of `test_verify_refuses_every_non_current_schema_version_with_one_diagnostic` (after line 1344 `    ]`, before the `# ====` banner at 1347). `hashlib`, `PROJECT_ROOT` and `VERDICT_SCHEMA_VERSION` are already imported at `:62`, `:67` and `:72`.

  ```python


  #: A-477: exactly one verdict schema is supported, so its bytes are frozen per
  #: version. An edit to `verdict.schema.json` without a VERDICT_SCHEMA_VERSION
  #: bump fails here; a bump fails with a KeyError until its digest is added.
  #: Carries forward the retired W9 gate phase's
  #: `test_shipped_schema_is_byte_identical_to_the_locked_v13_asset`.
  _VERDICT_SCHEMA_SHA256 = {
      13: "ade21cf0313d798b6b0ba9e41871bdcb310a33101c1b66317d1dbfc569e68734",
  }


  def test_the_shipped_verdict_schema_is_frozen_for_its_version():
      shipped = (PROJECT_ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_bytes()
      assert hashlib.sha256(shipped).hexdigest() == _VERDICT_SCHEMA_SHA256[VERDICT_SCHEMA_VERSION]
  ```

  - **Deliberate break (never commit it):** append one space to `src/assay/schemas/verdict.schema.json`. The new test must go red. Then run `git checkout -- src/assay/schemas/verdict.schema.json` and confirm `git diff -- src` is empty.
  - **Record it:**
    - Add to `W1-LOG.md` under Oracles: `W1R-1: schema freeze test added; break (one appended byte) -> red; reverted.`
    - Add to `W1-REPORT.md` under "What changed": `A-477 schema freeze: test_the_shipped_verdict_schema_is_frozen_for_its_version pins the v13 schema digest (carried forward from the retired W9 phase).`

### W1R-2 (MINOR): one assay-owned persisted schema has no refusal test

- **Where:** the hung-evidence record. `src/assay/liveness.py:1510-1512` writes `"schema_version": 1` with `"policy": "pressure-adjusted-idle-v1"`. On resume, `src/assay/mutation.py:1282-1283` (`_valid_hung_resource_evidence`) re-reads it from the state record.
- **Evidence:**
  - The brief's Work 7 table lists verdict, lane, shard, state record, resource snapshot and analysis manifest. It omits this record.
  - `git grep schema_version` over `tests/core/test_mutation_judge_identity.py`, `test_mutation_hung_evidence_persistence.py` and `test_liveness_runner_monitor.py` finds only fixtures that write `1`. No test flips the version.
  - A probe with the file's own `_hung_evidence()` helper returns True for the baseline and False for `schema_version` 0 and 2. The guard exists but nothing tests it, so A-477's "one test per schema" is not met for this record.
- **Fix:** in `tests/core/test_mutation_judge_identity.py`, insert the block below immediately before `def test_idle_hang_evidence_may_also_retain_a_candidate_finish_event():` (line 690). `pytest` and `mutation` are already imported at `:38` and `:41`.

  ```python
  @pytest.mark.parametrize("version", [0, 2])
  def test_hung_resource_evidence_with_a_non_current_schema_version_is_refused(version):
      """A-477: the one refusal test for the persisted hung-evidence record
      (`liveness.py` writes `schema_version: 1`; resume re-reads it through
      `_valid_hung_resource_evidence`). A non-current version is never trusted."""
      evidence = _hung_evidence()
      assert mutation._valid_hung_resource_evidence(evidence) is True
      evidence["schema_version"] = version
      assert mutation._valid_hung_resource_evidence(evidence) is False


  ```

  - **Deliberate break (never commit it):** at `src/assay/mutation.py:1283`, change `value["schema_version"] != 1` to `value["schema_version"] > 1`. The `[0]` case must go red. Revert and confirm `git diff -- src` is empty.
  - **Record it:**
    - Add to `W1-LOG.md` under Oracles: `W1R-2: hung-evidence refusal test; break (!= -> >) -> [0] red; reverted.`
    - Add to `W1-REPORT.md` under "What changed", in the refusal-test bullet: `hung-evidence record (liveness evidence schema 1)`.

### W1R-3 (MINOR): the new shard refusal cases are duplicates and match only a prefix

- **Where:** `tests/core/test_b105_mutation_boundaries.py`, the rows added at `:637-641` of `test_shard_merge_rejects_each_malformed_manifest_shape`.
- **Evidence:**
  - `MUTATION_STATE_SCHEMA_VERSION == 1` (`src/assay/mutation.py:220`), so the `- 1` row and the `schema_version=0` row are the same input. The collected ids `[documents2-…]` and `[documents3-…]` are both version 0.
  - Both rows match the prefix `"unsupported shard schema_version"`. They do not assert the full refusal (`mutation.py:1802-1805`: `unsupported shard schema_version 0; expected 1`), although the checklist asks for the exact refusal.
  - The break does reproduce: `!=` to `>` at `mutation.py:1801` turns both rows red.
- **Fix:** replace the two added rows with the text below and leave every other row unchanged:

  ```python
          (
              [_shard(schema_version=mutation.MUTATION_STATE_SCHEMA_VERSION - 1)],
              f"unsupported shard schema_version {mutation.MUTATION_STATE_SCHEMA_VERSION - 1}; "
              f"expected {mutation.MUTATION_STATE_SCHEMA_VERSION}$",
          ),
          (
              [_shard(schema_version=0)],
              f"unsupported shard schema_version 0; expected {mutation.MUTATION_STATE_SCHEMA_VERSION}$",
          ),
  ```

  I checked the full message against `str(MutationStateError)` and it matches: `'unsupported shard schema_version 0; expected 1'`.

### W1R-4 (MINOR): the B105 acceptance bullet still demands an `--ignore` of the deleted file

- **Where:** `nyxloom-trove/4-backlog.md:10729-10744`, the open `- [ ]` B105 criterion.
- **Evidence:**
  - W1 added its amendment at `:10734-10735`. The older A-468 amendment then follows at `:10736-10744`. It still says "both self-qualification lanes also `--ignore=tests/test_python_qualification.py` … It stays at its P33-locked path and the release lane keeps running it … The drift test pins the exact `--ignore` and `--deselect` sets."
  - Since `bb696f53`, both statements are false.
  - Because the amendments now run out of date order, a reader sees the 2026-09-28 text last and takes it as current.
  - pytest silently accepts `--ignore` of a nonexistent path, so anyone who follows this criterion would add a lane token that does nothing.
  - W4's backlog edit (`W4-test-split.md:134`) changes only the `pythonpath` value in that amendment.
- **Fix:**
  1. Delete lines `10734-10735`, which are W1's current two-line amendment.
  2. After the line ending ``--deselect` sets.`` (the last line of the A-468 amendment), insert exactly:

  ```
        **Amended 2026-09-29 (A-475, Wave A W1):** the deselections are gone
        because the two release-tag audit tests were removed, and
        `tests/test_python_qualification.py` is deleted, so the A-468 `--ignore`
        above never lands and no lane runs that file.
  ```

### W1R-5 (MINOR): the REPORT's Work 13 list misclassifies `tests/qualification/`

- **Where:** `nyxloom-trove/reports/wave-a/W1-REPORT.md:36`.
- **Evidence:**
  - The REPORT says `test_go_r1_real.py` is "not part of the registered gate collection (`tests/qualification/`)".
  - Collect-only on the branch lists 9 `tests/qualification/` items, including `tests/qualification/test_go_r1_real.py::test_a_real_go_lane_passes_and_records_the_toolchain_that_judged_it`. They are collected by `pytest tests`, which means the registered gate and both B105 lanes.
  - They are skipped only through the module `pytestmark` (`test_go_r1_real.py:79`, `ASSAY_GO_QUALIFICATION=1`). W4 moves them to `gate/tests/qualification/` (`W4-test-split.md:41`).
- **Fix:** in `W1-REPORT.md:36`, replace `environment-gated real-toolchain test, not part of the registered gate collection (`tests/qualification/`).` with `environment-gated real-toolchain test: collected by the registered gate and both B105 lanes but skipped unless ASSAY_GO_QUALIFICATION=1 (module pytestmark, test_go_r1_real.py:79); W4 moves it to gate/tests/qualification/.`

### W1R-6 (MINOR): B124 is marked DONE while its dstdns-checkout item is still open

- **Where:** `nyxloom-trove/4-backlog.md:11628`.
- **Evidence:**
  - B124's third item is "the dstdns-checkout dependency of SQL qualification (replaced by B126)". The brief (step 9) and CD31 give it to W5.
  - `tests/test_gate_qualify_dstdns_sql.py:93` still reads `/workspaces/dstdns` when that path exists (W1-REPORT, Work 13).
- **Fix:** replace `**Status: DONE (W1, merge hash pending: the controller fills it at merge; A-475).** Remove:` with `**Status: DONE (W1, merge hash pending: the controller fills it at merge; A-475) except the dstdns-checkout item, which closes with B126 (W5).** Remove:`.

### W1R-7 (MINOR): later briefs cite gate-script line anchors that W1 shifted

- **Where:**
  - `W4-test-split.md:24` cites `tools/tester-unified-gate.sh:791-821` (the entry points).
  - `W5-sql-self-contained.md:10` cites `:756-765` (the `docker run` block).
- **Evidence:** W1 removed 247 lines from `run_inner`, and the script went from 821 to 574 lines. On the branch, `# --- entry points` is at `:544` and `container_id="$(docker run -d \` is at `:509`. Anchors at or before line 452 are unchanged: W4's `:134-152` and `:364-370`, and W2's `:134-152` and `:302-362`. A strict implementer would stop BLOCKED on "an anchor does not re-resolve".
- **Fix:** append this line to `W1-REPORT.md` under "For decision":

  ```
  - Anchor shift for later briefs: tools/tester-unified-gate.sh lost 247 lines after :452. W4's :791-821 is now :544-574; W5's :756-765 is now :509-518. Earlier anchors are unchanged.
  ```

---

## Checklist results

### 1. Brief conformance

- Every Work 1 deletion is done: 7 P25 fixtures, the release wheel and its manifest, `qualify_topos.py`, `qualify_cmru_b006a.py`, and the three test files.
- Nothing forbidden was touched:
  - `git diff --stat 762b500d..wave-a-w1-retire -- carve-assets src gate/distribution run-gate.toml pyproject.toml tools/self-qualification-gate.sh tools/b105_report_check.py` is empty.
  - Nothing changed outside `assay/`.
- `snapshot_selection` and `release_wheel.py` are untouched.
- **Gate script:**
  - All seven blocks are gone.
  - The P26 command and its 8 `--deselect`s are byte-unchanged (the diff touches comments only).
  - The comments were rewritten as the brief instructs.
  - `/opt/tester-venv/bin/python` still appears exactly 4 times (`test_distribution_gate.py:70` holds).
  - The order in `run_inner` is `wheel-installed` (:394), `attestation-hardened` (:452), `run_self_hosted_lane` (:454), `run_independent_witness` (:456), lint (:462-463).
  - `bash -n` exits 0. `shellcheck` 0.x exits 0 on the branch, as it does on the base.
  - Every function is still called. Each `die`/validator is used; each pipeline function is called exactly once.
  - No variable from a removed phase is read later: `topos_marker` and `cmru_b006a_marker` were phase-local and went with their blocks, and `version`, `wheel`, `scratch`, `distribution` and `run_venv_site` all still have readers.
  - The only trap is the outer `cleanup_assay_gate_container`, which is unchanged.
  - Under `set -e`, `run_self_hosted_lane`'s explicit `return 1` still aborts `run_inner`, which is invoked unconditionally at `:548`.
- `assay.toml`: only the 4 `--deselect` lines and the two comments changed.
- `nyxloom.toml`: the change is the two comment lines.
- ROOT_PINNED lost exactly the three names.
- Docs follow the table: README, DESIGN-GUIDE (the B105 paragraph, §13 as one paragraph with its table kept, §15 with the new heading and 5 lines, §16 number kept), CONSUMERS and CHANGES.
- Decisions: all 13 listed rows are annotated (A-202..206, A-269 with "WI-5 harness only", A-278, A-435, A-222, A-224, A-226, A-229, A-318).

### 2. Coverage that disappeared (what each removal asserted, and where current behaviour is still covered)

| Removed | Asserted | Current-behaviour part still covered by |
|---|---|---|
| `verdict-v5-accepted` (P33) | 12 still-executed nodes: the v4 refusal, `ca6` source_roots spelling on frozen v5 templates, gate-wiring and handoff claims, locked symbols, the v4→v5 migration script, P25 v5 sibling projection, 5 `sweep_v4_consumers` tests, the migration manifest | Refusal: the new `test_verify_refuses_every_non_current_schema_version_with_one_diagnostic` and `test_verify_rejects_a_foreign_schema_version_as_a_version_problem` (`test_verdict_conformance.py:1305`). Declared source-root spelling: full-document comparison in `tests/core/test_runner_evaluate_r1.py:56-80`. Everything else is historical or about carve tooling. |
| `lane-schema-v2-successors-verified` | the 9 v2 successors against the wheel | The same file is collected by the self-hosted lane against the same wheel (17 ids in collection). |
| `verdict-v6-v12-hard-cut-verified` | collect-only of the W1..W8 suites; exact hard-cut message for every frozen v6..v12 template; `VERDICT_SCHEMA_VERSION == 13` | The message is covered by the new parametrized test. The literal 13 is kept at `test_verdict_conformance.py:1320-1324` and `test_verdict_schema_is_packaged.py:275`. The rest is historical. |
| `verdict-v13-p25-successors-verified` (W9) | (a) shipped schema byte-identical to the locked v13 copy; (b) v13 P25 templates schema-valid and verifying clean; (c) W8 P25 v12 templates are hard-cut | (a) **not covered, see W1R-1**. (b) `test_every_fixture_independently_validates_against_the_packaged_schema` and `test_verify_accepts_every_independently_schema_valid_fixture` (`test_verdict_conformance.py:390`, `:403`). (c) historical. |
| `verdict-v13-successors-verified` | `test_verdict_v13_successors.py` and `test_b106_reuse_and_witness.py` against the wheel | Both are collected by the self-hosted lane against the same wheel (11 and 53 ids). |
| `topos-qualified` + `test_python_qualification.py` (32) | Real Topos runs with the current wheel: NO_MEASUREMENT with EMPTY_COVERAGE, DIRTY_TREE (before and after the run), BASE_IS_HEAD, HEAD_CHANGED; FAIL with UNCOVERED_LINES and EXCLUDED_LINES; resolved base is not the declared tag; wrong source root; universal-PASS forgery; comment-only 0/0; whole-document template match; 1.2.5 hash-pinned install; `bash -n` of the gate | `tests/core/test_runner_run_lane.py` (EMPTY_COVERAGE, DIRTY_TREE, the post-command dirt test at `:1125`, HEAD_CHANGED); `tests/core/test_measurability_base_is_head.py`; `tests/adapters/python/test_canary_python_pipeline_nested_project.py` (UNCOVERED_LINES); `tests/adapters/python/test_adapters_python_union_fidelity.py:171` (EXCLUDED_LINES); `tests/core/test_cli_provenance_and_request_base.py:646-672` (symbolic ref resolved to the OID); `test_runner_evaluate_r1.py` (whole document); `test_distribution_release_wheel.py:78,239` (hash-pinned install); `test_distribution_gate.py:130` (`bash -n`); `test_self_hosting.py:135` (`assay verify` on a real artifact, the A-317 path) |
| `cmru-b006a-qualified` + `test_gate_qualify_cmru_b006a.py` (63) | Harness control flow with stubbed boundaries, plus one real `assay.isolation` omission probe | `tests/core/test_isolation_unsafe_symlink_omissions.py` (11 tests) and `test_runner_snapshot_selection.py::test_live_command_observes_exact_policy_in_every_unit` |
| `test_gate_harness_version_pins.py` (4) | Pins inside `gate/python/*.py` | The remaining `qualify_dstdns_sql.py` has no verdict or carve-asset pin (only the TOML `schema_version = 2` at `:923`), so the subject is gone, as the brief says. |
| The embargo tests (2) | Monorepo tag history against `c56a13ea` | Retired by A-475 and CD5. No replacement is wanted. |

### 3. Refusal checks

- Each check is real and, apart from W1R-3, exact:
  - **Verdict:** full-list equality with a literal f-string.
  - **Lane:** `re.escape` of the full "declares … understands …" sentence.
  - **Resource:** exact `("unknown", {})`, with the `("clear", {})` control in the same test.
  - **State record:** the rerun assertion.
- I reproduced the O3 breaks independently on a scratch copy of `src/`, without editing the worktree, and they failed exactly as the LOG says:
  - verdict: params 0, 3 and 12 red;
  - lane: params 0 and 1 red;
  - shard: both new rows red;
  - resource: red.
- The state-record break is plausible: `_RECORD_REJECTED` is reached only through `!=` at `mutation.py:1566-1569`.
- The new code uses no clock and no monkeypatching.
- The pre-existing state-record test does pass `clock=lambda: datetime.now(...)`, but it asserts nothing about time.
- There are no hollow asserts.
- The sparse-document short-circuit, which the old v3 test showed, is still covered by the kept real-v2 test at `:1305`.

### 4. Dangling references

- The brief's O2 `git grep` prints nothing.
- A whole-repository `git grep` (excluding `carve-assets`) for every deleted path, retired marker, `ASSAY_P25_TOPOS_QUALIFIED`, `ASSAY_B006A_CMRU_QUALIFIED`, `assay-1.2.5` and the embargo test names hits only these:
  - reports and handoffs (history);
  - `decisions.md` and `4-backlog.md` (see W1R-4, W1R-6);
  - prose in the W5 SQL harness and its test, which the brief says to leave;
  - two historical research scripts, `reports/b110/research/scripts/table.py` and `reports/wave-a/w3-scripts/coupling.py`, which are never executed.
- Nothing executable refers to a deleted file:
  - no `--deselect`, `--ignore` or `collect_ignore` in `run-gate.toml`, `pyproject.toml`, `conftest.py`, the `tools/` files or `gate/`;
  - no consumer of `ASSAY_GATE_PHASE` outside assay that expects a retired marker (`src/assay/analysis.py:857` is generic).
- The P26 suite that still runs mentions none of the removed items; its only gate-script reader is deselected.

### 5. Lint and packaging

- The gate-equivalent `pyflakes 3.4.0` over `src/assay` plus `tests/` (without `tests/fixtures`) is clean. `gate/` is outside the lint scope.
- Nothing in `gate/distribution`, `pyproject.toml`, `.gitignore` or `.gitattributes` refers to a deleted path.
- The wheel ships only `src/assay`.
- The sdist is built from tracked files by the setuptools-scm finder, so the deleted fixtures and the 232 KB 1.2.5 wheel leave it.

### 6. Docs and decisions

- README, DESIGN-GUIDE and CONSUMERS no longer promise any retired phase. Nothing links to the old §13 or §15 anchors. The §6 B006a heading is intact.
- **A-317, A-350, A-468 left unannotated:** this is not a W1 problem.
  - The brief forbids annotating rows it does not list and asks for them in the REPORT, and the implementer did exactly that.
  - A-317 is historical narrative. Its gap was closed by `tests/core/test_verify_layer_independence.py`, and `test_self_hosting.py:135` still sends a real artifact through `assay verify`.
  - A-350's decision stands; only its comparison sentence is stale.
  - A-468(a) is moot.
  - W4 already schedules "A-468 ((a) superseded, (c) done), A-130 and A-350 (paths)" (`W4-test-split.md:133`).
  - The live hazard is the backlog text, not the decision rows, and that is W1R-4.

### 7. Collection

- Branch: 5882 collected. Base (a detached temporary worktree at `762b500d`, now removed): 5975.
- By id, 126 were removed and 33 added, for a net of -93. That matches exactly:

| Change | Ids |
|---|---|
| Deleted: `test_python_qualification.py` | 32 |
| Deleted: `test_gate_qualify_cmru_b006a.py` | 63 |
| Deleted: `test_gate_harness_version_pins.py` | 4 |
| Deleted: the embargo tests | 2 |
| Verdict test (1 old, 4 new) | +3 |
| Lane test (1 old, 3 new) | +2 |
| Shard (10 re-indexed ids, 12 new) | +2 |
| State record (1 old, 2 new) | +1 |
| `test_docs_examples_and_vocabulary` (12 ids renamed by doc line shifts, 12 new) | 0 |

- Focused run on the branch: 124 passed. It covered the marker and shellcheck tests, `test_self_lane.py`, the refusal tests, `test_runner_snapshot_selection.py`, `test_import_contracts.py` and `test_lane_schema_v2_locked_successors.py`.

### 8. Combined-axis attack

- **Constructed failure:** the retired W9 schema lock combined with the new version-symbolic refusal test lets an unversioned `verdict.schema.json` edit through. The demonstration is 1315 schema tests green on the drifted schema; see W1R-1.
- **Attempts that found no failure:**
  - **Removed `--deselect`s against history readers.** `git grep` of the judge tests for `c56a13ea`, `rev-list`, `git log`, `HEAD~`, `tag` and `merge-base` finds hits only on `tmp_path` or `git_repo` fixtures. The one exception is `conftest.py:659-665`, which reads a snapshot root. Against the real repository, only `test_b105_report_check.py` (HEAD only), `test_self_hosting.py:122` (HEAD only, and ignored in B105) and `test_distribution_build_release.py` (tooling; it walks tags, and W4 removes it from B105) remain. `requires_parent_repository` has one user left (`test_distribution_build_release.py`). The B105 tooling (`b105_report_check.py`, `self-qualification-gate.sh`) has no dependency on the deselects.
  - **Removed phases against `run_independent_witness`'s pinned argv.** `test_self_hosting.py:171` pins `argv_effective` to the unchanged `[lanes.tester-unified]` argv, and `:483-486` pins only markers that still exist. `test_distribution_gate.py`'s bare `"carve-assets/W"` absence check against later packages: W5 calls `gate/python/qualify_sql.py`, and W2 adds `analysis-lane-passed`. Neither collides.

---

## Notes (no action required)

- The code commit's subject is `feat(assay): retire …`. cmru's generated changelog will therefore list the retirement under "Added". The hand-written `[Unreleased]` entry correctly says `chore`.
- `nyxloom.toml:148` says "emits four inner phase markers". The script emits six (`judge-provenance-bound-…` and `pyflakes-clean` included). This inaccuracy predates W1; the brief confines W1 to `:160-177`.

## Verdict

**MERGE-WITH-FIXES.** Apply W1R-1 through W1R-7 as written. The registered gate (CD44), run by the controller, must then show `wheel-installed`, `attestation-hardened`, `judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`, `independent-self-hosting-passed`, `pyflakes-clean` and `ASSAY_REGISTERED_GATE_COMPLETE=1`, with none of the seven retired markers and no `FAILED` line.
