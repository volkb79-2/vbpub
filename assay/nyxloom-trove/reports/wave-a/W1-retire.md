# W1 — Retire cross-project qualification and historical schema checks (B124 + B125)

| Field | Value |
|---|---|
| Backlog | **B124** (A-475) + **B125** (A-477) |
| Branch | `wave-a-w1-retire` from the current `assay-b110-landing` tip (>= `be803c3a`) **after W3 has merged**. Own worktree; merge `--no-ff`; afterwards delete the worktree and the branch |
| Depends on | **W3**. W3 moves judge tests into `tests/<component>/` (24 stay at root), so resolve every `tests/…` anchor **by file name** (`git ls-files ':(glob)tests/**/<name>'`; the `:(glob)` prefix is required or root files are missed) and enclosing function, never by line |
| Contract class | **2d**: exact edit map and prepared oracles; no design choice is left |
| Implementer | **Sonnet**, fresh session (operator rule) |
| Decisions | A-475, A-477 (supersede A-205 and the P25/P33 path locks); `CARVER-DECISIONS.md` CD5-CD8, CD29. A-468(a)'s `--ignore` never landed, so there is nothing to remove for it |

**Goal.** The gate and `tests/` stop running other projects' trees, old commits and frozen templates. One refusal test per assay-owned schema refuses non-current versions. **No `src/assay` change** (CD7 amended: ciu provenance schema 1 stays accepted; that removal moved to the v14 wave).

## Carver questions (all decided; `CARVER-DECISIONS.md` binds)
- **Q1.** Decided (CD5): delete the two release-tag audit tests (A-475) and their `--deselect` entries (Work 4, Work 3).
- **Q2.** Decided (CD8): "retired" means no longer executed; `nyxloom-trove/carve-assets/**` stays as history (A-222); tests pinning those paths go with the harness.
- **Also decided:** CD6 and CD7 (amended) defer `reuse.py`'s v12 cold start and ciu schema 1 to the v14 wave (Work 8, REPORT only).

## Context to read first
Anchors verified at `be803c3a`; paths relative to `assay/` unless prefixed `nyxloom-trove/`.
0. `nyxloom-trove/reports/wave-a/CARVER-DECISIONS.md` (whole file).
1. Plan `nyxloom-trove/reports/assay-WAVE-A-PLAN-2026-09-29.md` §2 and §4.
2. `nyxloom-trove/decisions.md:1020` (A-475) and `:1022` (A-477).
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

   In the comment at 424-431, say that the P26 successors now run only in the self-hosted lane. Rewrite the P26 comment at 406-419 (it cites "SHAPE coverage moves into P33's own suite (`test_p26_attestation_shapes_survive_v5`…)", a retired suite): "After A-477 the three template-coupled P26 nodes stay deselected; their historical successors are retired and not executed." Leave the P26 command and its 8 `--deselect`s (441-454) unchanged. The new order is `wheel-installed` → `attestation-hardened` → `run_self_hosted_lane` → `run_independent_witness` → lint. Keep exactly 4 `/opt/tester-venv/bin/python` uses in the function section (`test_distribution_gate.py:70` asserts it).
3. **`assay.toml`**. Delete the two `--deselect=` lines from **both** B105 lanes (85-86 and 193-194). Rewrite two comments:
   - 56-60: drop the P25 sentence. The 60m budget stays.
   - 71-77: no collected **judge** test reads history or tags (A-475); `test_distribution_build_release.py` leaves B105 collection with W4; `snapshot_history` stays `"full"` until W7.

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
| Verdict | `verify.py:3028-3042` | Replace `test_verify_rejects_a_v3_artifact_with_exactly_one_version_diagnostic` (1327-1342) with `test_verify_refuses_every_non_current_schema_version_with_one_diagnostic`, parametrized over `[0, 3, VERDICT_SCHEMA_VERSION - 1, VERDICT_SCHEMA_VERSION + 1]`: take `_load("r1_pass.json")`, set the version, and assert `verify_document(doc) == [the exact one-line message]`, with the message written as a literal f-string in the test (as at `test_verdict_conformance.py:1320-1324`), never built by a `verify` helper. Keep the real-v2 test at 1305 |
| Lane file | `config.py:1468-1485` | Replace `test_unknown_schema_version_is_rejected` with `test_a_non_current_lane_schema_version_is_refused`, parametrized over `[0, LANE_SCHEMA_VERSION - 1, LANE_SCHEMA_VERSION + 1]`; `match=` the full `declares schema_version = {v}; this assay understands schema_version = {LANE_SCHEMA_VERSION}` |
| Shard summary | `mutation.py:1801-1805` | in `test_b105_mutation_boundaries.py:73-83` (`test_shard_merge_rejects_each_malformed_manifest_shape`) add `([_shard(schema_version=mutation.MUTATION_STATE_SCHEMA_VERSION - 1)], "unsupported shard schema_version")` and the same with `0` (the file tests only 999 today) |
| Mutation-state record | `mutation.py:1566-1583` (refused, then re-run) | `test_mutation_progress_budget_plan.py::test_resume_reruns_a_state_record_after_a_routine_schema_version_bump` covers only `+1000`: add a case with `stale["schema_version"] - 1` (same rerun assertion) |
| Resource snapshot | `liveness_resources.py:179-180` | in `test_liveness_resources.py::test_compare_requires_a_pair_of_complete_same_identity_snapshots` add `assert compare_resource_snapshots(current, {**current, "schema_version": RESOURCE_SNAPSHOT_SCHEMA_VERSION - 1}) == ("unknown", {})` |
| Analysis manifest | `analysis.py:91-92,159` | exists: `test_analysis.py::test_check_archive_refuses_unsupported_empty_or_malformed_manifests` (W2 owns the file; W2 moves it, do not edit) |

8. **Old-version acceptance paths (the B125 inventory).** Change none of them. Copy this table into the REPORT under **"For decision"**:

| Code | Accepts | Disposition |
|---|---|---|
| `reuse.py:14,57-69`; `runner.py:6040-6045`; `cli.py:1870` | a v12 verdict as a `--reuse-from` cold start | **REPORT** (CD6): consumer-visible; deferred to the v14 wave (A-477 hands A-470 A9 there) |
| `adjudication.py:106-113` `(1, 2)` | ciu provenance schema 1 (ciu 6.0.3) | **REPORT, change nothing** (CD7 amended: deferred to the v14 wave; the only real green ciu reference, `carve-assets/W2/ciu-provenance-green-reference.json`, is schema 1 and frozen, and two pipeline tests use it byte for byte) |
| `mutation.py:1566-1583` | nothing; the old record is refused and re-run | keep |
| `provenance.py:177-179` | PEP 610's legacy `hash` key | keep: this is pip's format, not assay's |

9. **Do not touch:**
   - `snapshot_selection = "repository-minus-unsafe-symlinks"` is **product behaviour** (B105 lanes `assay.toml:100,211`, `ciu/assay.toml:49`, judge tests). Only the CMRU harness goes.
   - `gate/distribution/release_wheel.py`: used by `build_release.py:504-507`, documented at `CONSUMERS.md:154`.
   - The dstdns SQL harness (**W5**): no gate phase; wired only via `tests/test_gate_qualify_dstdns_sql.py` (reads `gate/python/fixtures/dstdns-sql/`, `carve-assets/W3/expected/…`). Leave its prose.
   - The carve-asset locks: `P25/test_acceptance.py:24-27,208-213` is never collected; `P33/test_acceptance_v5.py:406-417,502-503,794,835` ran only in `verdict-v5-accepted`. After step 2 only P26 still executes (Q2).
10. **Decisions.** Append `**[Superseded by A-475 (Wave A W1).]**` (or A-477) to the end of the decision cell; never rewrite it.
    - A-475: A-202 to A-206, A-278, A-435, and A-269 (its WI-5 harness only; the policy stands).
    - A-477: A-222, A-224, A-226, A-229, A-318.
    - `git grep` for every deleted path and retired marker. Annotate only the listed rows; any other decision row the grep hits goes in the REPORT under "For decision", not annotated.
11. **Backlog:** mark B124 and B125 `DONE (W1, <merge hash>)`. Append one amendment sentence to the tag-audit bullet of the B105 acceptance list (`nyxloom-trove/4-backlog.md:10729-10742`): the deselections are gone because the tests were removed.
12. **`nyxloom-trove/nyxloom.toml:160-177`:** replace this with two comment lines. The Topos phase is retired (A-475); `timeout_seconds` is unchanged and pinned by `test_self_lane.py`.
13. **Residual readers (for W7).** Run `git grep -nE "PROJECT_ROOT|REPO_ROOT|/workspaces/" -- tests/`. Classify each hit that runs git on the real repository or reads outside `assay/`. Expected:
    - `test_distribution_build_release.py`: clones HEAD, and setuptools-scm `describe` walks tags. Tooling; W4 moves it.
    - `test_gate_qualify_dstdns_sql.py`: reads `/workspaces/dstdns` when present (W5).
    - `test_b105_report_check.py`, `test_self_hosting.py:122`: HEAD only.

    Put the real list in the REPORT. **No judge test may read history** (`test_distribution_build_release.py` is tooling and leaves collection with W4).
14. **`ROOT_PINNED` (W3's `tests/core/test_import_contracts.py`).** Remove the three names you deleted (`test_python_qualification.py`, `test_gate_qualify_cmru_b006a.py`, `test_gate_harness_version_pins.py`) in this commit. Move nothing else: W4 moves the remaining root judge files and deletes the list (CD23 amended).

## Docs to sync
| File | Edit |
|---|---|
| `README.md:992-993` | drop the "two tag-ref audit tests remain…" clause |
| `docs/DESIGN-GUIDE.md:1988-1992`, `docs/CONSUMERS.md:67-70` | no collected judge test reads history or tags (A-475); full history remains until B128 |
| `docs/DESIGN-GUIDE.md:3109-3134` (§13) | one paragraph: consumers qualify their own use of assay in their own gates, and assay's gate tests assay only (A-475). Keep the heading and table |
| `docs/DESIGN-GUIDE.md:3268-3327` (§15) | heading `## 15. Cross-project qualification belongs to the consumer (A-475)`, then 5 lines at most. Keep the §16 number (`:763` cites it). Do not touch §6's `…-b006a` heading at `:1743` (linked from README:122 and CONSUMERS:2925); nothing links to the §13/§15 anchors |
| `CHANGES.md` `[Unreleased]` | `### Changed`: the gate retires the Topos and CMRU qualification and the historical schema phases (A-475/A-477); nothing in the product changes |

## Oracles
Breaks: apply locally, see red, revert, log; never commit.

| # | Observable | Negative / controlled break |
|---|---|---|
| O1 | the rewritten marker test passes; its absence check tests the bare substrings (`topos-qualified`, `cmru-b006a-qualified`, `verdict-v5-accepted`, `verdict-v6-v12-hard-cut-verified`, `verdict-v13-p25-successors-verified`, `verdict-v13-successors-verified`, `lane-schema-v2-successors-verified`) anywhere in the script, not only `echo '…'` | re-add `echo 'ASSAY_GATE_PHASE=topos-qualified'` (and a `"`-quoted variant) in `run_inner` → red |
| O2 | the command below prints nothing (its only permitted hits are the absence assertions in `test_distribution_gate.py`, excluded by the pathspec) | a leftover reference fails |
| O3 | the parametrized refusal tests, and the new shard (`-1`, `0`), state-record (`-1`) and resource-snapshot (`-1`) cases, pass | `verify.py:3028` `!=` → `>`: the verdict `-1` case fails; same in `config.py:1480`; `mutation.py:1801` → the shard `-1` case fails; `liveness_resources.py:179-180` → the resource `-1` assert fails |
| O4 | `test_self_lane.py` passes with the empty set and also asserts `not any(a == "--deselect" or a.startswith("--deselect") for a in lane.argv)` for **both** B105 lanes | re-add one `--deselect` (one- or two-token form) to one lane → red |
| O5 | the registered gate is green, shows the O1 markers, and shows none of the seven retired ones | a retired marker appears in the log → fail |
| O6 | the step 13 list is in the REPORT, with no judge-test history reader | — |

```bash
git grep -nE 'qualify_topos|qualify_cmru_b006a|test_python_qualification|release/P25|fixtures/P25|topos-qualified|cmru-b006a-qualified' -- tools tests gate assay.toml run-gate.toml pyproject.toml ':!*qualify_dstdns_sql.py' ':!tests/test_distribution_gate.py'   # O2 (W5 files excluded)
```

**Forbidden in any test you write (AUTHORING §3b, condensed; every rule applies):**
- **A. Speed.** No `monotonic()+N` deadline then assert; no `sleep` to wait; no asserts on elapsed time or iteration counts. A timeout is only a generous failsafe; a slow-machine red is a true red.
- **B. Order.** Restore process-global state (`os.environ`, logging, module attributes); no `monkeypatch.setattr` on a `__getattr__` proxy; no shared-state teardown.
- **C. Hollow.** No `pass` bodies, "nothing raised" tests or trivia asserts (call counts, private attributes, log strings); never weaken or delete an assertion.
- **D. Coverage.** No no-cover pragmas, even in comments.
- **E. Inputs.** No real network, clock-dependent values or registries.
- **F. Numbers.** No predicted coverage or mutation numbers.

## Scope
- **Touch:**
  - `tools/tester-unified-gate.sh`;
  - `assay.toml`: comments plus the 4 deselect lines;
  - the deletions in Work 1;
  - these tests: `test_runner_snapshot_selection`, `test_self_lane`, `test_distribution_gate`, `test_verdict_conformance`, `test_config_reject`, `test_b105_mutation_boundaries`, `test_liveness_resources` and `test_mutation_progress_budget_plan`, plus the docstring of `test_lane_schema_v2_locked_successors` (note that the P33 originals no longer run);
  - `tests/core/test_import_contracts.py`: only the three `ROOT_PINNED` names (Work 14);
  - the docs table above;
  - `nyxloom-trove/{nyxloom.toml,decisions.md,4-backlog.md}`;
  - `reports/wave-a/W1-{LOG,REPORT}.md`.
- **Forbid** (needing any of these is BLOCKED):
  - `src/assay/**` and `carve-assets/**`; `gate/distribution/**`;
  - the dstdns SQL files named in step 9 (W5);
  - `tools/self-qualification-gate.sh` and `tools/b105_report_check.py` (W4);
  - `run-gate.toml` and `pyproject.toml`; `/workspaces/vbpub/{cmru,topos,ciu}/**`.

## Gate
```bash
docker ps   # no other session's gate container may be running
cd <worktree>/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > ../W1-gate.log 2>&1; echo "exit=$?"
```
Read the markers in a **separate** step (`grep -E 'ASSAY_GATE_PHASE=|ASSAY_REGISTERED_GATE_COMPLETE|FAILED' ../W1-gate.log`).

**No known-red exception (CD29).** The B107 test was removed by `35adca38` (in landing since `1e3c8a49`). The gate must be fully green: any `FAILED` line is BLOCKED.

**Host-load rule** (a production game server shares this host): nice/ionice everything; one gate container at a time, none while another session's gate runs; never the full `self-qualification` lane; remove containers by exact name. Editor tools only (`git rm` is fine). Trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

## BLOCKED rule
Write `BLOCKED: <reason>` to `W1-LOG.md`, commit, and stop if: W3 has not merged; an anchor does not re-resolve; a removed file has a consumer outside Scope; a step needs a `src/assay` change; a test that stays in `tests/` after W4 runs git on the real repository beyond `HEAD` or reads outside `assay/`; the gate has any `FAILED` line. Never improvise.

## Review
A fresh-session adversarial review before merge checks git state, not the REPORT: no retired marker or path remains in executed code; P26 and its deselections are intact; no `carve-assets/**` byte changed; the O3 breaks were observed; step 8 items are reported, not acted on. It reruns step 13.
