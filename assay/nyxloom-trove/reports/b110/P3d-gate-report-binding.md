# B110-P3d — B105 gate passes `--cold-witness`; source-bound report checker binds the v14 evidence

| Field | Value |
|---|---|
| Backlog | **B114** (B110 umbrella) |
| Branch | `assay-b110-p3d-gate`, off the current `assay-b110-v14` tip. The integration protocol is in `P3a-v14-schema-verify.md`. |
| Depends on | **P3b** merged into `assay-b110-v14` (`--cold-witness`, `--r2-manifest`, and `r2_command`/`evidence` produced). **P0** merged into the integration line and then into `assay-b110-v14`: its `b105_report_check.py` refusals of shard and partial scope are extended here, not duplicated. |
| Contract class | **2c** |
| Implementer | Sonnet (fresh session) |
| Decisions | A-470 (D6), A-468 (the B105 lane argv after P1), A-474 (checker refusals) |
| Size | M |

**What this package is.**
- The registered `self-qualification` gate invokes its R2 with the cold-witness policy and retains the ordered R2 manifest sidecar.
- `tools/b105_report_check.py`, which is B105's **source-bound** check that runs after `assay verify`, binds the v14 facts to the exact source revision. It re-derives the declared argv from `git show <commit>:assay/assay.toml`, re-applies the transform **independently**, requires the cold policy, recomputes the manifest digest from the sidecar, and checks every cold kill's witness node against the manifest position.

`assay verify` checks internal consistency; this checker is what makes "the transform matches the lane that was actually committed" true. The backlog says "a self-reported argv hash is not source proof".

---

## Context to read first

Paths are relative to `assay/`, verified at `db85f747`.

- `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §5 (the wire contract, the `collection_sha256` definition) and §9.3.
- `nyxloom-trove/reports/b110/P3a-v14-schema-verify.md`: the packet (the `R2Command`, `MutantEvidence` and X-rules; the `collection_digest` definition).
- `nyxloom-trove/reports/b110/P3b-r2-command-cold-witness.md`: "Owned interfaces" (`--r2-manifest`; the sidecar format is one node ID per line, each followed by `\n`, UTF-8).
- `tools/b105_report_check.py` (the whole file, ~150 lines):
  - `verify_report_document` at 14-107: exit, commit, lane, rigor, tree via `git rev-parse <commit>^{tree}` at ~46-61, PASS, claims, version, judge provenance;
  - `main` / argparse at 110-147.
- `tools/self-qualification-gate.sh`:
  - 150-213: `run_and_verify_lane` (the `assay run` invocation at ~181-188, `assay verify` at ~193, the checker call at ~196-207);
  - 216-246: the lane sequencing and the `B105_*` echoes;
  - 175-179: the qualification lane `unset`s the four `ASSAY_B105_*` variables.
- `run-gate.toml`: 33-52, the `self-qualification` lane and its `artifacts` list at 46-52.
- `tests/test_b105_report_check.py`:
  - `_git_value` at 23;
  - `_verifier_valid_report` at 34-75, which splices `r2_pass.json` into `pass.json` and must stay verifier-valid;
  - `_verify_with_assay_cli` at 78;
  - `_run_checker` at 94;
  - the tests at 142-211.
- `tests/test_self_lane.py`:
  - ~150-170: the `artifacts` pins for both lanes;
  - ~170-205: the gate-script substring pins (`"--resume" in script`, `'"$scratch/source/assay/tools/b105_report_check.py"' in script`, …).
- `tests/fixtures/verdicts/r2_pass_cold_witness.json`, the carver fixture created by P3a.

---

## Implementation packet (normative)

### Gate script changes (`tools/self-qualification-gate.sh`)

Inside `run_and_verify_lane`, build the `assay run` argv per lane. **Only** the `self-qualification` lane gets the new flags:

```bash
  local r2_manifest_path=".assay/r2-manifest-$lane.txt"
  local -a lane_flags=()
  case "$lane" in
    self-qualification)
      lane_flags+=(--cold-witness --r2-manifest "$r2_manifest_path")
      # B110-P6: pass --campaign-deadline "$campaign_deadline_path" here (P6 adds it).
      # B110-P10b: pass --equivalence-audit "$ledger_audit_path" here when a ledger is declared (P10b adds it).
      ;;
  esac
  ...
  if "$assay_bin" run "$lane" --file assay.toml \
      --require-judge-provenance \
      --resume \
      --progress "$progress_path" \
      --state-dir "$state_path" \
      --verdict-json "$verdict_path" \
      "${lane_flags[@]}"; then
```

- The checker call gains `--r2-manifest "$r2_manifest_path"`, passed **only** for `self-qualification`.
- After a verified qualification lane, echo `B105_R2_MANIFEST=$r2_manifest_path`, next to the existing `B105_VERDICT=`/`B105_PROGRESS=` echoes at ~245-246.
- `run-gate.toml` `lanes.self-qualification.artifacts` gains `".assay/r2-manifest-self-qualification.txt"`, appended after the existing five entries.

### Checker interface (`tools/b105_report_check.py`)

- **The new CLI argument** is `--r2-manifest PATH`, optional in argparse. It is **required** when `--expected-rigor` contains `R2`. When missing, the error is `"--r2-manifest is required for an R2 report"`, with exit 2 like the other refusals. It is **refused** when R2 is not in the rigor (`"--r2-manifest given for a report without R2"`).
- **`verify_report_document(..., r2_manifest: Path | None = None)`** is a new keyword-only parameter. When R2 is in the expected rigor, it runs `_check_v14_r2(document, repo_root, commit, lane, r2_manifest)` after the existing checks.

`_check_v14_r2` checks the following, in this order. **Each check has its own refusal message, which tests assert as a substring:**

| # | Check | Refusal substring |
|---|---|---|
| C1 | `document["judgment"]["r2"]["cold_witness_kills"] is True` | `B105 R2 requires cold_witness_kills true` |
| C2 | `rc = judgment.r2.r2_command` is a dict | `B105 R2 report has no r2_command` |
| C3 | the **source-bound lane argv**: `git -C <repo_root> show <commit>:assay/assay.toml`, parsed with `tomllib`, `lanes[<lane>].argv` as a list. It must equal `document["argv_declared"]` **and** `rc["argv_declared"]` | `declared argv differs from assay.toml at <commit>` |
| C4 | `rc["transform"] == "assay-r2-pytest-nocov/1"` (a checker constant) | `unexpected R2 transform` |
| C5 | **independent transform** (the checker's own code; it does **not** import `assay.r2_command`): drop every `--cov-branch`, every `--cov=<non-empty>` and every `--cov-report=<non-empty>`; refuse any remaining token starting `--cov` or `--no-cov`. The result must equal `rc["argv_transformed"]` | `argv_transformed is not the transform of the committed argv` |
| C6 | `rc["appended"] == ["-p", "no:pytest_cov"]` | `unexpected R2 appended argv` |
| C7 | `rc["cwd"] == "assay"` | `unexpected R2 cwd` |
| C8 | `rc["coverage_baseline"]["collection_sha256"] == rc["r2_baseline"]["collection_sha256"]`, the counts are equal, and both `duplicates == 0` | `R2 and coverage baseline collections differ` |
| C9 | none of `ASSAY_B105_COVERAGE_SOURCE`, `ASSAY_B105_COVERAGE_ARCHIVE_DIR`, `ASSAY_B105_SOURCE_COMMIT`, `ASSAY_B105_SOURCE_TREE` is a key of `document["env_effective"]` | `archive-hook variables present in the qualification run` |
| C10 | **manifest sidecar:** read the bytes. They must be empty or end with `b"\n"`. Split on `b"\n"` (dropping the final empty piece); every line must be non-empty, valid UTF-8 and contain no `\r`. Recompute the digest **independently**: `sha256(b"".join(str(len(l)).encode() + b":" + l + b"," for l in lines))`. It must equal `rc["r2_baseline"]["collection_sha256"]`, and `len(lines) == rc["r2_baseline"]["collection_count"]` | `R2 manifest sidecar does not match r2_baseline` |
| C11 | for **every** outcome in `claims[R2].mutation.killed` whose `execution.mode == "witness-cold"`: `ev = outcome["evidence"]`; `ev["failed_call_index"] + 1 == ev["started_count"]`; `0 <= ev["failed_call_index"] < len(lines)`; `lines[ev["failed_call_index"]].decode() == outcome["execution"]["witness"]["node_id"]` | `cold kill <candidate_id> is not the manifest's node at its failed index` |
| C12 | every native outcome's `evidence` with `command == "r2"` has `collection_sha256 == rc["r2_baseline"]["collection_sha256"]`. This deliberately repeats verify's X5/X6 as source-side defence; it is cheap | `candidate evidence collection differs from r2_baseline` |

P0's shard and partial-scope refusals (`shard_index`/`shard_count` present, or `candidate_ids` smaller than the lane's plan) run **before** C1. P3d does not re-implement them.

**Ledger TODO.** If `judgment.r2.equivalence_ledger` is not null, refuse with `ledger binding not implemented (B110-P10b)`. P10b replaces this line with its binding (the ledger file at `<commit>` hashes to `equivalence_ledger.sha256`, and the audit receipt is bound). This keeps a declared ledger fail-closed until then.

### Serialized examples

**Sidecar, valid** (three IDs):
```
tests/test_x.py::test_a\ntests/test_x.py::test_b\ntests/test_x.py::test_c\n
```
Its digest is `sha256(b"23:tests/test_x.py::test_a,23:tests/test_x.py::test_b,23:tests/test_x.py::test_c,")`, where 23 is the byte length of each ID.

**Sidecar, invalid:**
- missing the final `\n`;
- contains an empty line;
- CRLF line endings;
- the IDs reordered (digest mismatch).

### Required flow (the gate, `self-qualification` lane)

1. `assay run … --cold-witness --r2-manifest .assay/r2-manifest-self-qualification.txt`. P3b writes the sidecar only after its own digest check.
2. `assay verify "$verdict_path"`.
3. On producer exit 0: the checker with `--r2-manifest`. Any refusal → `return 2`, as today.

### Topology

- The checker runs in the gate container with the run-venv `python`.
- `--repo-root "$scratch/source"` is the exact-OID private clone, so `git show <commit>:assay/assay.toml` reads the **committed** lane, not the worktree.
- The sidecar path is relative to the project dir (`assay/`), the same base as `.assay/verdict-*.json`.

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| valid v14 report accepted | checker | `_verifier_valid_report("self-qualification", R0–R3)` rebuilt at v14 (below) plus a 3-line sidecar → checker exit 0; `assay verify` also `[]` | `tests/test_b105_report_check.py` | — |
| C1–C12 each refuse | checker | one test per row: mutate exactly the named field of the valid report or sidecar → a non-zero exit and the named substring | same | remove C3 → the "argv edited after commit" test passes → red |
| independence from the producer | checker | a parametrized table of 8 argv cases compares the checker's transform with `assay.r2_command.transform_argv`. They must agree on each case, and **both** refuse `--cov src` (two tokens) | same | the checker imports `assay.r2_command` → an AST test that the checker source has no `assay.r2_command` import goes red |
| source-bound, not self-reported | checker | build the report with `argv_declared` = the lane argv **plus** `--deselect=extra`; C3 refuses even though the report is internally consistent (`assay verify` returns `[]` for it) | same | — |
| manifest position | checker | swap two sidecar lines → C10 refuses. Rewrite the witness `node_id` to line 0 while `failed_call_index` stays 1 → C11 refuses | same | — |
| rigor gating | checker | the preflight lane (R0,R1) with `--r2-manifest` → refused; without it → unchanged acceptance | same | — |
| gate wiring | script | `tests/test_self_lane.py` pins: `--cold-witness` appears **only** inside the `self-qualification)` case branch; `'--r2-manifest "$r2_manifest_path"'` occurs twice (run and checker); the two TODO markers `B110-P6:` and `B110-P10b:` are present; `B105_R2_MANIFEST=` is echoed. `run-gate.toml` artifacts pin updated | `tests/test_self_lane.py` | pass `--cold-witness` for the preflight → the case-branch pin goes red |

**Building the valid v14 report (normative, `tests/test_b105_report_check.py`):**
1. Start from P3a's `tests/fixtures/verdicts/r2_pass_cold_witness.json` as the R2 claim/judgment source, spliced into `pass.json` as `_verifier_valid_report` does today.
2. Set the top-level `argv_declared` = `argv_effective` = the **actual** `lanes.self-qualification.argv` read from `git show HEAD:assay/assay.toml` (the test's `_git_value` already resolves HEAD), with `argv_appended = []`.
3. Set `r2_command.argv_declared` to the same list, and `argv_transformed` to the transform of it.
4. Set both baselines' `collection_count = 3` and `collection_sha256` = the digest of the 3-line sidecar written to `tmp_path`.
5. Set the witness-cold kill's `evidence` to the same digest, `started_count: 2`, `failed_call_index: 1`, and `witness.node_id` = line 1.
6. Assert `_verify_with_assay_cli` gives `[]` **before** asserting the checker's result. If `assay verify` rejects it, fix the builder, never the checker.

### Degrees of freedom

Private helper names inside the checker, and the bash variable names **other than** the pinned substrings. The refusal substrings, the argument names and the sidecar format are fixed.

---

## Work

1. Create a worktree under `/workspaces/vbpub/.worktrees/`, on `assay-b110-p3d-gate` from `assay-b110-v14`. Confirm that `assay run --help` in the branch lists `--cold-witness` and `--r2-manifest`, and that `tools/b105_report_check.py` carries P0's shard refusal. If either is missing → BLOCKED.
2. **Red first.** Extend `tests/test_b105_report_check.py` (the v14 valid-report builder, C1–C12, independence, rigor gating) and `tests/test_self_lane.py` (the pins). Run them and record the failures.
3. Implement `_check_v14_r2` and the `--r2-manifest` argument in `tools/b105_report_check.py`.
4. Edit `tools/self-qualification-gate.sh` per the packet: flags only in the qualification branch, the checker argument, the echo and the two TODO markers.
5. Add the artifact entry to `run-gate.toml`.
6. Docs:
   - `docs/CONSUMERS.md` B105 note (:70-92): the qualification gate now runs R2 with `--cold-witness` and retains the manifest sidecar;
   - `docs/DESIGN-GUIDE.md` B105 section (~:1959-1981): the source-bound checker re-derives the transform and the manifest position, and why (a self-reported argv is not source proof);
   - CHANGES `### Changed`.
7. Run the focused tests, the gate and the preflight, then write the report.

## Oracles

The traceability rows are the oracles. Each has its observable, its controlled break or named mutation as the negative, and the gate (tester-unified, plus the preflight for the gate-script and run-gate changes). The oracles that must be demonstrated red first are:
- C3 (source-bound);
- C10 and C11 (the manifest);
- the preflight-refuses-manifest rigor gate.

### Forbidden oracle patterns (AUTHORING.md §3b, verbatim)

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

## Scope / forbid

- **Touch:** `tools/b105_report_check.py`, `tools/self-qualification-gate.sh`, `run-gate.toml` (the one artifact line), `tests/test_b105_report_check.py`, `tests/test_self_lane.py`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `CHANGES.md`.
- **Forbid:**
  - `src/assay/**` (if the checker needs a producer change → BLOCKED);
  - `assay.toml`;
  - `tools/tester-unified-gate.sh` (the release gate);
  - the preflight lane's flags;
  - any change to how the gate computes `source_commit`/`source_tree`;
  - running the full `self-qualification` lane.

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused suites serially:
   `nice -n 19 ionice -c3 python -m pytest tests/test_b105_report_check.py tests/test_self_lane.py -q -p no:cacheprovider`
2. Run `bash -n tools/self-qualification-gate.sh` and `shellcheck tools/self-qualification-gate.sh`, if shellcheck is installed; do not install it.
3. Run the gate:
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p3d-gate.log 2>&1; echo EXIT=$?`
4. In a separate step, read `ASSAY_GATE_CONTAINER_EXIT=0` and `ASSAY_REGISTERED_GATE_COMPLETE=1`.
5. Run `python ./run-gate.py self-qualification-preflight`. It must pass unchanged, because the preflight lane gets no new flags.
6. **Do not** run the `self-qualification` lane. Its first cold run is the controller's pilot (plan §7). The pilot will exercise these flags end to end.

## BLOCKED rule

If a named contract cannot be met as specified, STOP. This applies when:
- P3b's CLI flags or sidecar are absent or differ from the specified format;
- P0's refusals are absent;
- the valid report cannot be made `assay verify`-clean without changing `src/`.

Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P3d-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

## Report

Write `nyxloom-trove/reports/assay-B110-P3d-REPORT.md` with:
- the commits;
- the traceability table with actual tests and red-first counts;
- the gate and preflight log paths and markers;
- residuals, including the P6/P10b TODO markers left for those packages.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. Do not merge; a fresh-session review (never a fork) precedes the controller's `--no-ff` merge into `assay-b110-v14`.
