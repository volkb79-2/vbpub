<!-- Verbatim round-2 pre-dispatch review output (fresh-session, read-only reviewer), 2026-09-28, against 3ee09b61. Dispositions: ../REVIEW-2026-09-28-round2.md -->

**Round-2 pre-dispatch review of P3a–P3d at `3ee09b61`**

**Verdicts:** P3a READY-WITH-FIXES · P3b NOT READY (two blocking findings) · P3c READY-WITH-FIXES · P3d READY-WITH-FIXES.

The review was read-only. I ran no tests, gates or docker. I made two niced `verify_document` calls on in-memory JSON, plus source and pytest/pluggy inspection. I checked about 70 new anchors against source (unchanged since `db85f747`) and all were accurate except the P1-moved paths noted below.

## P3a

**Round-1 status**

| ID | Status | Evidence / remaining gap |
|---|---|---|
| P3A-1 | RESOLVED, see P3A2-2 | :75, :480–487; forbid carve-out :715 |
| P3A-2 | RESOLVED | :294, :409; plan §5 corrected |
| P3A-3 | RESOLVED via C1 | :64, :324, :378; forbid :717 |
| P3A-4 | RESOLVED | :176–179, :275, :342 |
| P3A-5 | RESOLVED | :315, :322, :372 (W3 has 0 equivalents; see P3A2-2) |
| P3A-6 | RESOLVED | :355, :396–401 |
| P3A-7 | RESOLVED, see P3A2-4 | :319, :341, :400 |
| P3A-8 | RESOLVED | 15-row table :123–141; per-field negatives :397 |
| P3A-9 | RESOLVED, see P3A2-1 | :73, :77, :473, :494–495, :508–510, :518 (all anchors verified) |
| P3A-10, 11, 12 | RESOLVED | :51/:318; :176–188/:343–348; :339/:401 |
| P3A-13 | PARTIAL | :9 and :24 reconciled. Fixtures are still recipes (:407), with gaps (P3A2-3). |

**New findings**

- **P3A2-1 MAJOR: P1 path drift.** P3a lands on a v14 base that already contains P1, but its paths are still `db85f747`'s.
  - `tests/test_standalone.py` (:512, :526) is now `tests/zz_slow/test_standalone.py`.
  - The `test_cli_run.py` pins at 576–577, 672 and 760 (:526, :405) belong to tests P1 moved into `zz_slow/test_cli_run_real_campaigns.py`.
  - The new marker order (:508) drops P1's `v13_real` (`zz_slow/test_b106_witness_real_runs.py`) ordering pin.
  - The dataclass-contract fixture (:451) exists only after P1, but P3a "may start earlier".
  - **Fix:** name the zz_slow locations, keep P1's pin under the v14 names, and say "regenerate the contract after the one rebase onto v14".
- **P3A2-2 MAJOR: the W3 regeneration step cannot be run (:487).** `qualify_dstdns_sql.py` shells out to docker with `postgres:18-alpine`, and no registered lane runs it. The step invites a container outside the gate on the shared host.
  - The raw W3 witness also contains placeholders (`@HEAD_OID@`, `@STARTED@`), so "must verify `[]`" (:315, :372, :398) fails unless those are substituted first.
  - W3 has **0** equivalents (killed 1, survived 5), so it is not an X8(b)-with-equivalents case.
  - **Fix:** drop the end-to-end confirmation. Validate the migration with `test_compare_with_witness_accepts_the_frozen_witness_round_tripped` plus `verify_document` on `_witness_as_actual()`. Correct the W3 labelling.
- **P3A2-3 MINOR: fixture recipe gaps (:410, :419).**
  - It sets the top-level `argv_declared` but not `argv_effective`, which breaks the `argv_effective == argv_declared + argv_appended` invariant (`verdict.py:4665`).
  - `r2_pass.json` already has **two** killed entries, so "add a second killed entry" is ambiguous.
  - "make `judgment.r2` exactly plan §5's block" would drop producer, jobs and operators; it should say "merge".
- **P3A2-4 MINOR: X12 contradiction.** :375 says an absent `r2_command` or `equivalence_ledger` fails "(model)", but :302 says X12 is raw-only, and reconstruction via `.get` makes the model accept an absent key when cold is false. **Fix:** make it raw-only.

**Verdict: READY-WITH-FIXES.**

## P3b

**Round-1 status**

| ID | Status | Evidence / remaining gap |
|---|---|---|
| P3B-1 | RESOLVED for proof failures | :50, :344, :86. The timeout route re-creates the defect (P3B2-1). |
| P3B-2 | RESOLVED | :466 (`def test_é`), :467. Verified: pytest 9.1.1 `main.py` keeps duplicate file arguments under `--keep-duplicates`. |
| P3B-3 | PARTIAL | :283 is fine; the ordering at :284–285 is wrong (P3B2-3). |
| P3B-4 | RESOLVED | :236–253, :273–276 |
| P3B-5 | RESOLVED, minor residual | :135–140, :302–319 (P3B2-7) |
| P3B-6 | NOT RESOLVED | :345, :468 (P3B2-1) |
| P3B-7 | RESOLVED for cold; wrong for declared | :153–160 (P3B2-2) |
| P3B-8 | RESOLVED, caveats | :463–472 (P3B2-1, P3B2-8) |
| P3B-9 to P3B-13 | RESOLVED | :198–234 and 1a; :464/:482; :377; :264; :720 |

**New findings**

- **P3B2-1 BLOCKING: the R2-baseline timeout verdict fails `assay verify` (:345, :468).**
  - The brief routes the timeout to the `except AssayError` at ~4541, producing a payload-free R2 `BUDGET_EXCEEDED/LANE_TIMEOUT` beside R0 PASS, and claims "A-432 already accepts" it. A-432 is about R3.
  - `LANE_TIMEOUT` is not in `_INDEPENDENT_R2_TERMINALS`, so `_check_r2_rederivation` compares it with R0.
  - I ran `verify_document` on `r2_budget_exceeded_lane_timeout.json` with the R2 payload and the judgment removed. It returns: "R2 claim status (BUDGET_EXCEEDED, LANE_TIMEOUT) disagrees with … (PASS, None)".
  - P6's `remaining()` also raises `LANE_TIMEOUT` on SIGTERM, so a SIGTERM during the R2 baseline hits the same path.
  - The oracle fixture is also impossible: with a 2 s lane budget and a sleeping test, R0 times out first, and 2 s is timing-dependent (§3b-A).
  - **Fix:** re-raise the R2-baseline timeout through the 4532 tuple (a dedicated `R2BaselineTimeoutError` with `BUDGET_EXCEEDED/LANE_TIMEOUT`). Then 5385 `refuse_all` renders it whole-lane, as `cli.py:1527` already does. For the test, block only when pytest-cov is not loaded, or use a fake `process_runner` that returns `LANE_TIMEOUT` for the `r2-baseline` phase.
- **P3B2-2 BLOCKING: declared survivors can never happen when the lane's argv has `--cov` (B105's does) (:153–160, :418).**
  - `survivor_proof_ok` requires "not unsupported" for the declared attempt too.
  - The receipt's `unsupported` is `not _STANDARD_LOOP`. The trust check accepts only `_pytest.*`/witness/liveness implementations, and pytest-cov 7.1.0 registers a `pytest_runtestloop(wrapper=True)` whenever `--cov` is given. The brief says so itself at :390.
  - So every declared PASS becomes `crashed`, which turns FAIL/MUTANTS_SURVIVED into ERROR/EXEC_FAILED and hides survivors in screens.
  - The tests would not notice: a declared survivor is producible only on a lane without `--cov`.
  - **Fix:** for the declared proof, drop `not unsupported`. The hook-fingerprint equality with the coverage baseline, which carries the same pytest-cov wrapper, pins the hook set. Add a real-run declared-survivor test on a lane with `--cov`.
- **P3B2-3 MAJOR: evidence-requiredness check ordering (:284–285).** "Records from a non-cold campaign never reach this check … the **last** check rejects them" is false. The judge check is literally last (`mutation.py:1364–1401`), so a missing-evidence check placed with the shape checks raises `MutationStateError` for non-cold `/3` records, and for P6 deadline-unbound records. **Fix:** state it runs only after the judge-identity check (and P6's deadline check) pass.
- **P3B2-4 MAJOR: `liveness_plugin_path` is elided (:352, :340).** The liveness plugin implements `pytest_runtest_logreport` and `pytest_sessionfinish`, and the witness trusts them only when `ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH` is set. On B105 (liveness auto, so active), omitting it makes every R2 baseline `unsupported`, which refuses the whole lane. Most tests use `liveness="false"` and would not catch it. **Fix:** pin `liveness_plugin_path=` for both injections, plus a liveness-active cold test.
- **P3B2-5 MAJOR: P1 drift.** P1 moved the B106 real-run tests (680–998, including the lookalike adversary at 820–924) into `zz_slow/test_b106_witness_real_runs.py`. P1's exact-name guard forbids adding tests there. The focused command (:707) omits that file. Where P3b's heavy real-run tests belong in the tier layout is undecided. **Fix:** name the file and decide the tier placement (and `EXPECTED_SLOW_FILES` if needed).
- **P3B2-6 MAJOR: merge order with P4 (:9).** "Whichever merges second rebases onto the other" is impossible: P4 is on the integration line, P3b is on v14, v14 is never rebased, and P4's brief never mentions P3b. **Fix:** add a Work step 1 BLOCKED check that P4 is in v14 (plan §11.6 order), or name who resolves the conflict at the v14 merges.
- **P3B2-7 MINOR: the 4532 tuple literal drops P6's entry (:344).** The literal omits P6's `CampaignPlanMismatchError`, which is already in the base, so copying it verbatim removes P6's clause. **Fix:** extend the existing tuple.
- **P3B2-8 MINOR: single-dash clusters can bypass the static table (:310–314).** A value-taking option embedded in a cluster (`-qprandomly`, `-qoaddopts=-n4`) escapes both the order and override rows; runtime proof catches it later. **Fix:** parse clusters properly.
- **P3B2-9 MINOR: two unclear points.**
  - `--r2-manifest` refusals (:292, :469): the `_resolve_state_dir` pattern is a pre-run `LaneConfigError` with no verdict, yet the oracle says "whole-lane BAD_LANE_CONFIG". Pick one.
  - :321 ("a lane that declares a ledger + `--shard` refused") conflicts with plan §11.7 ("screens with a declared ledger may still shard").

**Verdict: NOT READY** (P3B2-1, P3B2-2).

## P3c

**Round-1 status:** P3C-1 to P3C-7 are RESOLVED:

| ID | Evidence |
|---|---|
| P3C-1 | :119–127, :196 |
| P3C-2 | :9, :23, :230, :422 |
| P3C-3 | :90–92, :161, :222 |
| P3C-4 | :201, :205–212 |
| P3C-5 | :198 |
| P3C-6 | :135, :143, :199 |
| P3C-7 | :414 |

P3C-8 is RESOLVED except for the runtime-inactive claim (P3C2-2).

I verified the fake-clock arithmetic: `_CpuSampleHistory` decides at t = W, so the 6.0/8.0 firing ticks and the 5/7 breaks hold. The :209 test uses flat CPU (3.0), so the `clock.t == 5.0` pin survives.

**New findings**

- **P3C2-1 MAJOR: the properties contradict each other (:108 vs :137).** :108 says they return "the parsed value or the liveness-module default" (`-> float`); :137 says they return "None when undeclared".
  - The effective values must come from `runner._cpu_window_s`, which is private, and from "the calibration's resolved floor", but `LivenessCalibration` (a NamedTuple) has no floor field.
  - The default-exactness oracle (:196) reads private attributes (§3b-C).
  - **Fix:** properties return `float | None`; `LivenessRunner` exposes a public `cpu_window_s`; `LivenessCalibration` gains `idle_floor_s`; the disclosure uses those.
- **P3C2-2 MINOR: combined fixture 4 is impossible (:100, :270).** `inject_liveness_plugin` goes inactive only for `false`, `true`+non-pytest or auto+non-pytest, and rows 6 and 7 plus RW-36 now refuse all of those at load. **Fix:** drop the fixture, or name a monkeypatch seam and correct :100.

**Verdict: READY-WITH-FIXES.**

## P3d

**Round-1 status:** P3D-1 to P3D-6 are all RESOLVED:

| ID | Evidence |
|---|---|
| P3D-1 | :9, :67, :221 |
| P3D-2 | C13 :159; SB/CS labels :141–159 |
| P3D-3 | :230 (matches P0 W5's shape) |
| P3D-4 | :222, :420 |
| P3D-5 | :213 |
| P3D-6 | :156, :122, :215, :76, :155, :85, :130 |

The C9 wiring matches P6: the digest of the raw bytes, the `YYYY-MM-DDTHH:MM:SSZ` form and the name grammar all agree, and so does D3's key list.

**New findings**

- **P3D2-1 MAJOR: the sourcing option runs the gate's heavy setup (:222).** "Source the script with a guard … placed before the lane sequencing" cannot skip anything, because `run_and_verify_lane` is defined only after the clone (:53), venv and pip builds (:68, :126). Sourcing would run those in a unit test on the shared host. **Fix:** mandate the `sed` extraction. Also say the stub `run-venv/bin/python` must print an integer for P6's `remaining_s`.
- **P3D2-2 MINOR: wrong test path (:410).** The focused command names `tests/test_b105_report_check_real_plan.py`; after P1 it is `tests/zz_slow/…`.
- **P3D2-3 MINOR: C12 is stricter than X7 (:158).** C12 applies to every `command:"r2"` evidence, but X7 allows non-matching facts on prefix, crashed and ERROR outcomes, so a legitimate report could be refused. **Fix:** limit C12 to `survived` and `witness-cold` (the X5/X6 scope).

**Verdict: READY-WITH-FIXES.**

## Carver decisions and dependencies

**Consistent across the four briefs and plan §5:**
- C2, C3, the C9 split between P3a and P3d, the X8 split, a non-null active `reason`, and the 6/7-key baselines with `wall_s` omitted.
- Judge identity: P3b's cold flag plus seven optional inputs matches A-470's canonical list of 7 facts plus the ledger.
- The 25-key receipt (the existing 10 at `mutation_witness.py:308-321` verified).
- The evidence rules.
- `--cold-witness` with `--shard` allowed; `--equivalence-audit` appears only as P3d's P10c marker.

**Inconsistent:**
- C1 holds for proof failures, but the timeout route breaks it (P3B2-1).
- The declared-survivor proof conflicts with pytest-cov (P3B2-2).
- The P3b ledger + `--shard` rule conflicts with plan §11.7 (P3B2-9).

**Dependencies** match plan §4/§11.6: P3b needs P6 in base, P3c needs P1+P2, P3d needs P6, and P3a rebases exactly once. The exceptions are the P4 ordering (P3B2-6) and the P1 path drift in P3a, P3b and P3d.