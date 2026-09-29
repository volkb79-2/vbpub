# W1 — Retire cross-project qualification and historical schema checks (B124 + B125)

| Field | Value |
|---|---|
| Backlog | **B124** (A-475) + **B125** (A-477) |
| Branch | `wave-a-w1-retire` from `assay-b110-landing` **after W3 has merged**. Own worktree; merge `--no-ff`; afterwards delete the worktree and the branch |
| Depends on | **W3**. W3 may move judge tests into `tests/<component>/`, so resolve every `tests/…` anchor **by file name** (`git ls-files 'tests/**/<name>'`) and enclosing function, never by line |
| Contract class | **2d**: exact edit map and prepared oracles; no design choice is left |
| Implementer | **Sonnet**, fresh session (operator rule) |
| Decisions | A-475, A-477 (supersede A-205 and the P25/P33 path locks). A-468(a)'s `--ignore` never landed, so there is nothing to remove for it |

**Goal.** The gate and `tests/` stop running other projects' trees, old commits and frozen templates. One refusal test per assay-owned schema refuses non-current versions. **No `src/assay` change.**

## Carver questions (answered before dispatch; the brief assumes the recommendation)
- **Q1.** Delete the two release-tag audit tests? They read tags and commit `c56a13ea` from history (A-475, bullet 4). **Recommended: yes** (Work 4).
- **Q2.** Does A-477's "retired" mean *no longer executed*, with the `nyxloom-trove/carve-assets/**` files kept as the historical record (A-222)? **Recommended: yes. Delete nothing there.**

## Context to read first
Anchors were verified at `5bbd916e`; paths are relative to `assay/`.
1. Plan `reports/assay-WAVE-A-PLAN-2026-09-29.md` §2 and §4.
2. `decisions.md:1020` (A-475) and `:1022` (A-477).
3. `tools/tester-unified-gate.sh:372-711` (`run_inner`) and `:791-821` (the entry points).
4. `tests/test_distribution_gate.py:66-74` and `:224-268`; `tests/test_self_lane.py:93-136`.
5. `tests/test_runner_snapshot_selection.py:48-59` and `:774-919` (EOF).
6. `assay.toml:56-90` and `:185-198`.
7. `tests/test_verdict_conformance.py:1305-1342`; `tests/test_config_reject.py:121-126`.

## Work
1. **Delete** with `git rm`:

| Path | Why |
|---|---|
| `gate/python/qualify_topos.py`, `gate/python/fixtures/P25/` (7 files), `gate/python/release/P25/` (the 1.2.5 wheel and its `release-manifest.json`) | the Topos P25 harness and its only inputs |
| `gate/python/qualify_cmru_b006a.py` | the CMRU harness. It writes `_b006a_probe.py` only into a disposable copy; nothing in `/workspaces/vbpub/cmru` exists for it |
| `tests/test_python_qualification.py`, `tests/test_gate_qualify_cmru_b006a.py` | the tests of those two harnesses |
| `tests/test_gate_harness_version_pins.py` | its only pinned subject was `qualify_topos.py`, so both `found` asserts would fail (A-435) |

2. **`tools/tester-unified-gate.sh`**, in `run_inner`. Delete these blocks:
   - 456-524: `verdict-v5-accepted` (the P33 suite);
   - 526-536: `lane-schema-v2-successors-verified`;
   - 538-627: `verdict-v6-v12-hard-cut-verified`;
   - 629-636: `verdict-v13-p25-successors-verified` (W9);
   - 638-655: `verdict-v13-successors-verified`. Both of its files stay in `tests/` and already run in the self-hosted lane;
   - 659-679: `topos-qualified`;
   - 681-701: `cmru-b006a-qualified`.

   In the comment at 424-431, say that the P26 successors now run only in the self-hosted lane. Leave the P26 command and its 8 `--deselect`s (441-454) unchanged. The new order is `wheel-installed` → `attestation-hardened` → `run_self_hosted_lane` → `run_independent_witness` → lint. Keep exactly 4 `/opt/tester-venv/bin/python` uses in the function section (`test_distribution_gate.py:70` asserts it).
3. **`assay.toml`**. Delete the two `--deselect=` lines from **both** B105 lanes (85-86 and 193-194). Rewrite two comments:
   - 56-60: drop the P25 sentence. The 60m budget stays.
   - 71-77: no collected test reads history or tags since A-475; `snapshot_history` stays `"full"` until W7.

   Change no other token.
4. **`tests/test_runner_snapshot_selection.py`**. Delete the embargo section from `:774` to EOF: both `@requires_parent_repository` tests and the helpers `_REPO_ROOT`, `_WI1_LANDING_COMMIT`, `_repo_git`, `_SCHEMA_IN_REPO`, `_WI4_POLICY_RECORD`, `_is_ancestor` and `_carries_wi4_policy_record`. Remove the imports this leaves unused (`REPO_ROOT`, `requires_parent_repository`) and any docstring text about the embargo audit.
5. **`tests/test_self_lane.py:116-127`**: the deselection pin becomes `assert deselected == set()`.
6. **`tests/test_distribution_gate.py::test_gate_script_preserves_required_markers_and_hardens_the_build`**. Replace lines 226-254 (every marker, order and v13-probe assert) with three checks:
   - required: `echo 'ASSAY_GATE_PHASE=<m>'` for each of `wheel-installed`, `attestation-hardened`, `self-hosted-lane-passed`, `independent-self-hosting-passed` and `pyflakes-clean`, plus `echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'`;
   - **absent** anywhere in the script: the seven retired markers, and the strings `qualify_topos.py`, `qualify_cmru_b006a.py`, `carve-assets/P33` and `carve-assets/W`;
   - order inside `run_inner`: `attestation-hardened`, then `run_self_hosted_lane "$worktree"`, then `run_independent_witness "$scratch"`.

   Keep lines 256-268 verbatim.
7. **One refusal test per assay-owned schema.**

| Schema | Guard | Test |
|---|---|---|
| Verdict | `verify.py:3028-3042` | Replace `test_verify_rejects_a_v3_artifact_with_exactly_one_version_diagnostic` (1327-1342) with `test_verify_refuses_every_non_current_schema_version_with_one_diagnostic`, parametrized over `[0, 3, VERDICT_SCHEMA_VERSION - 1, VERDICT_SCHEMA_VERSION + 1]`: take `_load("r1_pass.json")`, set the version, and assert `verify_document(doc) == [the exact one-line message]`. Keep the real-v2 test at 1305 |
| Lane file | `config.py:1468-1485` | Replace `test_unknown_schema_version_is_rejected` with `test_a_non_current_lane_schema_version_is_refused`, parametrized over `[0, LANE_SCHEMA_VERSION - 1, LANE_SCHEMA_VERSION + 1]`; `match=` the full `declares schema_version = {v}; this assay understands schema_version = {LANE_SCHEMA_VERSION}` |
| Shard summary | `mutation.py:1801-1805` | exists: `test_b105_mutation_boundaries.py::test_shard_merge_rejects_each_malformed_manifest_shape` |
| Mutation-state record | `mutation.py:1566-1583` (refused, then re-run) | exists: `test_mutation_progress_budget_plan.py::test_resume_reruns_a_state_record_after_a_routine_schema_version_bump` |
| Resource snapshot | `liveness_resources.py:179-180` | exists: `test_liveness_resources.py::test_compare_requires_a_pair_of_complete_same_identity_snapshots` |
| Analysis manifest | `analysis.py:91-92,159` | exists: `test_analysis.py::test_check_archive_refuses_unsupported_empty_or_malformed_manifests` (W2 owns the file) |

8. **Old-version acceptance paths (the B125 inventory).** Change none of them. Copy this table into the REPORT under **"For decision"**:

| Code | Accepts | Disposition |
|---|---|---|
| `reuse.py:14,57-69`; `runner.py:6040-6045`; `cli.py:1870` | a v12 verdict as a `--reuse-from` cold start | **REPORT**: consumer-visible. A-477 hands A-470 A9 to the v14 wave |
| `adjudication.py:106-113` `(1, 2)` | ciu provenance schema 1 (ciu 6.0.3) | **REPORT**: a foreign producer's schema; consumer-visible |
| `mutation.py:1566-1583` | nothing; the old record is refused and re-run | keep |
| `provenance.py:177-179` | PEP 610's legacy `hash` key | keep: this is pip's format, not assay's |

9. **Do not touch:**
   - `snapshot_selection = "repository-minus-unsafe-symlinks"` is **product behaviour**. The B105 lanes (`assay.toml:100,211`) and `ciu/assay.toml:49` use it, and judge tests cover it. Only the CMRU harness goes.
   - `gate/distribution/release_wheel.py`: `build_release.py:504-507` uses it and `CONSUMERS.md:154` documents it.
   - The dstdns SQL harness (**W5**). It has no gate phase. Its only wiring is `tests/test_gate_qualify_dstdns_sql.py`, which reads `gate/python/fixtures/dstdns-sql/` and `carve-assets/W3/expected/…` (`:60`). Leave its prose as it is.
   - The carve-asset locks: `P25/test_acceptance.py:24-27,208-213` is never collected; `P33/test_acceptance_v5.py:406-417,502-503,794,835` ran only in `verdict-v5-accepted`. After step 2 only P26 still executes (Q2).
10. **Decisions.** Append `**[Superseded by A-475 (Wave A W1).]**` (or A-477) to the end of the decision cell; never rewrite it.
    - A-475: A-202 to A-206, A-278, A-435, and A-269 (its WI-5 harness only; the policy stands).
    - A-477: A-222, A-224, A-226, A-229, A-318.
    - `git grep` for every deleted path and retired marker. List any further row you annotate in the REPORT.
11. **Backlog:** mark B124 and B125 `DONE (W1, <merge hash>)`. Append one amendment sentence to the B105 acceptance bullet at `4-backlog.md:10726`: the tag-audit deselections are gone because the tests were removed.
12. **`nyxloom-trove/nyxloom.toml:160-177`:** replace this with two comment lines. The Topos phase is retired (A-475); `timeout_seconds` is unchanged and pinned by `test_self_lane.py`.
13. **Residual readers (for W7).** Run `git grep -nE "PROJECT_ROOT|REPO_ROOT|/workspaces/" -- tests/`. Classify each hit that runs git on the real repository or reads outside `assay/`. Expected:
    - `test_distribution_build_release.py`: clones HEAD, and setuptools-scm `describe` walks tags. Tooling; W4 moves it.
    - `test_gate_qualify_dstdns_sql.py`: reads `/workspaces/dstdns` when present (W5).
    - `test_b105_report_check.py`, `test_self_hosting.py:122`: HEAD only.

    Put the real list in the REPORT. **No judge test may read history.**

## Docs to sync
| File | Edit |
|---|---|
| `README.md:992-993` | drop the "two tag-ref audit tests remain…" clause |
| `docs/DESIGN-GUIDE.md:1988-1992`, `docs/CONSUMERS.md:67-70` | no collected test reads history or tags (A-475); full history remains until B128 |
| `docs/DESIGN-GUIDE.md:3109-3134` (§13) | one paragraph: consumers qualify their own use of assay in their own gates, and assay's gate tests assay only (A-475). Keep the heading and table |
| `docs/DESIGN-GUIDE.md:3268-3327` (§15) | heading `## 15. Cross-project qualification belongs to the consumer (A-475)`, then 5 lines at most. Keep the §16 number (`:763` cites it) and the `…-b006a` anchor |
| `CHANGES.md` `[Unreleased]` | `### Changed`: the gate retires the Topos and CMRU qualification and the historical schema phases (A-475/A-477); nothing in the product changes |

## Oracles
Breaks: apply locally, see red, revert, log; never commit.

| # | Observable | Negative / controlled break |
|---|---|---|
| O1 | the rewritten marker test passes | re-add `echo 'ASSAY_GATE_PHASE=topos-qualified'` in `run_inner` → red |
| O2 | the command below prints nothing | a leftover reference fails |
| O3 | both parametrized refusal tests pass | change `verify.py:3028`'s `!=` to `>` → the `-1` case fails. Same edit in `config.py:1480` → its `-1` case fails |
| O4 | `test_self_lane.py` passes with the empty set | re-add one `--deselect` to one lane → red |
| O5 | the registered gate is green, shows the O1 markers, and shows none of the seven retired ones | a retired marker appears in the log → fail |
| O6 | the step 13 list is in the REPORT, with no judge-test history reader | — |

```bash
git grep -nE 'qualify_topos|qualify_cmru_b006a|test_python_qualification|release/P25|fixtures/P25|topos-qualified|cmru-b006a-qualified' -- tools tests gate assay.toml run-gate.toml pyproject.toml ':!*qualify_dstdns_sql.py'   # O2 (W5 files excluded)
```

**Forbidden in any test you write (AUTHORING §3b, condensed; every rule applies):**
- **A. Speed.** No `monotonic()+N` deadline followed by an assert. No `sleep` to let something happen. No asserting on elapsed time or on iteration counts. Wait on real sync points; a timeout is only a generous failsafe. A red on a slow machine is a true red: fix the test, never widen a timeout.
- **B. Order.** Restore any process-global state (`os.environ`, logging, module attributes). Never `monkeypatch.setattr` on a `__getattr__` proxy. Never tear down shared state.
- **C. Hollow.** No `pass` bodies and no "nothing raised" tests. Never assert trivia (call counts, private attributes, log strings). Never weaken or delete an assertion.
- **D. Coverage.** No no-cover pragmas; the gate rejects the token, even in comments.
- **E. Inputs.** No real network, no clock-dependent values, no real registries.
- **F. Numbers.** No predicted coverage or mutation numbers.

## Scope
- **Touch:**
  - `tools/tester-unified-gate.sh`;
  - `assay.toml`: comments plus the 4 deselect lines;
  - the deletions in Work 1;
  - these tests: `test_runner_snapshot_selection`, `test_self_lane`, `test_distribution_gate`, `test_verdict_conformance` and `test_config_reject`, plus the docstring of `test_lane_schema_v2_locked_successors` (note that the P33 originals no longer run);
  - the docs table above;
  - `nyxloom-trove/{nyxloom.toml,decisions.md,4-backlog.md}`;
  - `reports/wave-a/W1-{LOG,REPORT}.md`.
- **Forbid** (needing any of these is BLOCKED):
  - `src/assay/**` and `carve-assets/**`;
  - the dstdns SQL files named in step 9 (W5);
  - `gate/distribution/**`;
  - `tools/self-qualification-gate.sh` and `tools/b105_report_check.py` (W4);
  - `run-gate.toml` and `pyproject.toml`;
  - `/workspaces/vbpub/{cmru,topos,ciu}/**`.

## Gate
```bash
docker ps   # no other session's gate container may be running
cd <worktree>/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > ../W1-gate.log 2>&1; echo "exit=$?"
```
Read the markers in a **separate** step (`grep -E 'ASSAY_GATE_PHASE=|ASSAY_REGISTERED_GATE_COMPLETE|FAILED' ../W1-gate.log`).

**Known red on main (plan §4).** `tests/test_cli_run.py::test_run_liveness_classifies_a_thread_join_hang_as_hung` fails; it is the B107 regression that `612843ef` addresses. The gate is acceptable only if this is the sole failure and the log shows it. Record that in the LOG.

**Host-load rule** (a production game server shares this host):
- use nice/ionice for everything;
- at most one gate container at a time, and none while another session's gate runs;
- never run the full `self-qualification` lane;
- remove containers by exact name only.

Edit with the editor tools only (`git rm` is fine). Trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

## BLOCKED rule
Write `BLOCKED: <reason>` to `W1-LOG.md`, commit, and stop if any of these holds:
- W3 has not merged;
- an anchor does not re-resolve;
- a removed file has a consumer outside Scope;
- a step needs a `src/assay` change;
- the gate is red for any reason besides the known test.

Never improvise.

## Review
A fresh-session adversarial review before merge checks git state, not the REPORT. It confirms that:
- no retired marker or path remains in executed code;
- P26 and its deselections are intact;
- no `carve-assets/**` byte changed;
- the O3 breaks were really observed;
- the step 8 items are reported, not acted on.

It also reruns step 13.
