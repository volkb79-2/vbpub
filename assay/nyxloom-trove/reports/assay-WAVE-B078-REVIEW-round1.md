# assay B078 Checkpoint 1 — adversarial review, round 1

Reviewer: fresh session (no implementer context inherited).
Branch: `feat/assay-b078-r0-structured-report-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b078-r0-structured-report`
Range reviewed: `9c2c435f..b03b0afc` (6 commits, 16 files, +2118/-12).
Method: blind read of design (SR-0..SR-6) + backlog B078 acceptance box +
full diff and base-vs-branch call-graph trace, BEFORE reading the
implementer's LOG/REPORT. Live probes and implementation mutation run
against the worktree; tree restored clean afterwards (`git status` empty).

---

## Verdict: **REJECT**

**4 blockers.** The work itself is high quality — the module structure, the
completeness core, the reservation discipline, the RecursionError guard and
the fault-injection matrix are all genuinely good, and the tests are not
hollow (proven by mutation, §Hollow-test probes). The rejection rests on one
fact: **Checkpoint 1 as shipped does not fix RG-45's confirmed live
reproduction**, because the lane that reproduces it does not take the code
path that was wired. Everything else is downstream of that or is a
documentation/invariant accuracy problem.

---

## The headline finding: the implementer's correction is HALF right, and the
## wrong half is the load-bearing one

The implementer flagged (LOG §"The one place I did not follow the design
document") that the design's wiring pointer was wrong. I verified this
independently, and split it in two.

### The half that is RIGHT — confirm and keep

`runner.py:1140-1142` **at merge-base `9c2c435f`** is indeed the tail of
`execute_command`'s *docstring* ("Otherwise the exit code decides: 0 is
`PASS`; anything else is `FAIL`/`COMMAND_FAILED` (A-073 ...)"), not the rule.
The rule is implemented in `execute_plan` (base `runner.py:1066-1088`).

Further, `execute_command` is **not called by `runner.py` at all** on the
base — it is public API. `run_lane`'s direct branch calls `execute_plan`
itself (base `runner.py:5299`). So wiring only `execute_command`, as the
design and the wave prompt both instructed, would have left `run_lane`
untouched. **Moving the branch into `execute_plan` behind a keyword-only
`result_report=None` was the correct architectural call**, and the design
document's SR-6 / "Migration surface" pointer should be corrected on main.

### The half that is WRONG — this is Blocker 1

The implementer's justification for *why* the direct path is the one that
matters is a factual claim, stated three times as settled fact:

- `runner.py:5474-5481` (code comment): *"dstdns's `ui_unit` is an R0-only
  `kind = "assay"` lane"*
- `tests/test_runner_result_report.py:502-503` (test docstring): same claim
- `nyxloom-trove/reports/assay-WAVE-B078-REPORT.md:29`: *"dstdns `ui_unit`,
  an R0-only `kind = "assay"` lane"*

It is not true. `/workspaces/dstdns/assay.toml:1379-1381`:

```toml
[lanes.ui_unit]
scope = "S1"
rigor = ["R0", "R1"]
```

`ui_unit` declares **R0 *and* R1**. Per `run_lane`'s own dispatch
(`runner.py`, base 5112: `if r1_declared or r2_declared or r3_declared:`),
an R1 lane never reaches the direct branch — it goes to
`_run_higher_rigor_lane` → `_run_prepared_lane` → `_execute_snapshot_unit`,
whose `execute_plan` call (`src/assay/runner.py:2815`) was **not** wired, and
whose result is what `build_r0_claim` turns into the R0 claim
(`_run_prepared_lane`, base `runner.py:3185`).

The direct R0-only branch the wave wired is real and worth wiring — but it is
not the branch RG-45 reproduces on.

---

## Blockers

### BLOCKER 1 — the tiebreak never reaches the R0 command of any R1/R2/R3 lane, so RG-45's confirmed live repro is still unfixed

**Evidence.** `src/assay/runner.py:2815` (`_execute_snapshot_unit`) calls
`execute_plan` with no `result_report=`, and that call's `CommandResult` is
what becomes the R0 claim for every lane declaring R1 or higher. Wired sites
are only `runner.py:1328` (`execute_command`) and `runner.py:5482` (direct
R0-only).

**Proven live**, not inferred. I added a throwaway probe test (an R0+R1 lane,
`FakeAdapter`, real `/bin/sh` writing a verified-complete 140-test/0-failure
vitest document then `exit 1` — the exact RG-45 shape that
`test_run_lane_direct_r0_path_applies_the_tiebreak` asserts PASSes on the
R0-only path) and ran it under `pytest`:

```
AssertionError: R0 claim is FAIL / COMMAND_FAILED -- the B078 tiebreak
did NOT reach the R0+R1 path
```

The probe file was removed after the run; the tree is clean.

**Why this is a blocker and not an observation.** B078's own acceptance box
(`4-backlog.md:8122-8128`) scopes Checkpoint 1 to "vitest, **the confirmed
live repro**". The confirmed live repro is `ui_unit`. The REPORT ticks that
box (`4-backlog.md:8129` "**Shipped 2026-09-08**") on the strength of a test
whose docstring asserts a false premise about which lane shape that repro
has. Every test in the branch is green and the feature does nothing for the
case the backlog item exists for — which is precisely the failure mode the
implementer correctly warned about one layer up ("would have shipped a
feature that passes every unit test and does nothing for the case it was
built for"), landing one layer further in.

**Prescription.** Wire `_execute_snapshot_unit`'s baseline execution
(`runner.py:2815`) to receive the lane's `result_report`, and *only* the
baseline — R2 candidate re-executions and R3 canary halves must keep the
default (see Decision ask D-1 below on the exact seam, since
`_execute_snapshot_unit` is the shared engine for all three roles and the
distinction currently rides on the caller, e.g. `progress_phase="baseline"`).
Add an end-to-end test mirroring
`test_run_lane_direct_r0_path_applies_the_tiebreak` but with
`rigor=("R0", "R1")` and a real judge, plus its must-fail control. Correct the
three false "ui_unit is R0-only" statements at `runner.py:5474-5481`,
`tests/test_runner_result_report.py:502-503`, and REPORT line 29.

Note for the fix round: `safeio.reserve_output`'s `create_missing_parents=True`
(`src/assay/safeio.py:414`, B006(b)) is contractually available to "a call site
that KNOWS it owns an ephemeral, assay-managed snapshot" — which the snapshot
path is, unlike the direct path. That legitimately removes the parent-directory
limitation for the snapshot half (see OBS 3).

---

### BLOCKER 2 — `result_report` is declarable on any lane, with no rigor coupling and no diagnostic, so on an R1+ lane it is silently inert

**Evidence.** `src/assay/config.py`'s `_load_result_report` validates format
and path only; I confirmed by introspection that `rigor` appears nowhere in
it, and `_load_lane` calls it unconditionally. So today a lane author writes:

```toml
[lanes.ui_unit]
rigor = ["R0", "R1"]

[lanes.ui_unit.result_report]
format = "vitest-json"
path = "vitest-report.json"
```

...and assay accepts it, emits nothing, and behaves exactly as if the table
were absent — forever, with no way to tell from the verdict.

This is the same hazard the implementer's own registry comment names and
guards against for formats (`result_reports/__init__.py`: *"a hand-listed
copy is how a format becomes declarable before its reader exists"*). The
identical shape exists one axis over, for rigor, and is unguarded.

It is listed separately from Blocker 1 because fixing Blocker 1 does not
automatically settle it: if the controller decides the tiebreak should apply
to the baseline only and not, say, to some future path, the "declarable but
inert" surface still needs either coverage or a load-time refusal.

**Prescription.** After Blocker 1 is wired, confirm no lane shape remains
where the declaration is accepted and inert. If any remains, refuse it at
load in `_load_result_report` with a message naming the rigor levels it is
supported on — the same discipline `_check_cwd_is_not_under_a_link_path` and
the isolation/rigor conditional already apply in that module.

---

### BLOCKER 3 — the branch's own stated invariant about R3 canary halves is false; `execute_command`'s new forwarding reaches them

**Evidence.** `execute_plan`'s new docstring (`runner.py:1073-1085`) states:

> Only the two callers that run *the lane's own R0 command once* pass this
> argument (`execute_command` and `run_lane`'s direct R0-only path); every
> other call site keeps the default and is therefore unchanged by
> construction

and `docs/DESIGN-GUIDE.md:572-575` states the tiebreak *"applies to the
lane's own R0 command alone ... R3's canary halves have their own semantics —
neither takes this input."* The LOG repeats it: *"R3's canary control/transform
halves ... keep the default and are unchanged by construction."*

But `src/assay/canary.py:248` — `_run_pipeline`, the shared engine **both**
the control and the transformed half of `run_python_canary` run through —
calls `execute_command(lane, ...)`, and `execute_command` now unconditionally
forwards `lane.result_report` (`runner.py:1328`). So on a lane declaring
`result_report`, both canary halves DO take this input.

`run_python_canary` is the legacy standalone path (not the shipped
`run_isolated_canary`), but it is public API — the branch's own base-code
comment at `canary.py:236-247` says so explicitly and treats it as worth
fixing for exactly this reason (B029/DA-R6). There is no test:
`grep -rl result_report tests/` returns only `conftest.py` and the three new
files; no canary test touches it.

The behavioural reach may well be harmless or even desirable, but it is
**unstated, unreviewed, untested, and directly contradicted by three places
in this branch's own prose**. "Unchanged by construction" is the claim doing
the safety work in SR-1, and it is currently not true as written.

**Prescription.** Either (a) pass `result_report=None` explicitly from
`canary.py:248` and keep the stated invariant true, or (b) accept that the
legacy canary halves take the input, and correct `runner.py:1073-1085`,
`DESIGN-GUIDE.md:572-575` and the LOG to say so — with a test pinning the
chosen behaviour. This is a product decision, not mine to make: see D-2.

---

### BLOCKER 4 — CONSUMERS.md tells consumers the verdict records the overridden `returncode`; it does not

**Evidence.** `docs/CONSUMERS.md:308`:

> A `PASS` that overrode a non-zero exit still records the real `returncode`
> and keeps the command's output tails, so the disagreement stays visible on
> the verdict.

and the matching code comment at `runner.py:1225-1232` ("`returncode` really
is non-zero on the artifact").

`returncode` is a field on the in-process `CommandResult` only. It is **not**
on the verdict: `grep -c returncode src/assay/schemas/verdict.schema.json`
returns `0`, and `grep -c returncode src/assay/verdict.py` returns `0`.
`assemble_verdict` (`runner.py:2071-2093`) threads `result_stdout_tail`,
`result_stderr_tail` and the two dropped-byte counts — and no exit code. The
only place `returncode` is recorded is the **progress stream's**
`command_finished` event (`CONSUMERS.md:1946`), which exists only when the
consumer opted into `--progress`.

This is consumer-facing documentation of a schema field that does not exist,
in the one paragraph a consumer would read to decide whether this feature is
auditable. It is a small fix but it is not cosmetic: it is the branch's
entire answer to "was diagnostic information dropped?".

**Prescription.** Correct `CONSUMERS.md:308` and `runner.py:1225-1232` to
state what is actually true — the output tails are retained on the verdict
(and a `PASS` carrying tails is itself the signature of an overridden exit
code, since a plain green run omits them), and the exit code itself is visible
only in the progress artifact's `command_finished` event. If the controller
wants the exit code on the verdict, that is a schema change and therefore
explicitly out of Checkpoint 1's scope — see D-3.

---

## Decision asks for the controller (not mine to decide)

- **D-1.** Blocker 1's seam. `_execute_snapshot_unit` is the shared engine for
  the baseline R0 run, R2 candidates and R3 canary halves. Should the
  `result_report` be passed by the *caller* (`_run_prepared_lane`'s baseline
  call site only), or should `_execute_snapshot_unit` gain a parameter it
  forwards? The former keeps "only callers that run the lane's own R0 command
  once" literally true; the latter centralises it. Related: should the
  snapshot path use `create_missing_parents=True` (which B006(b)'s own
  contract permits for an assay-owned ephemeral snapshot, unlike the direct
  path), which would make the parent-directory limitation direct-path-only?
- **D-2.** Blocker 3. Should the legacy `run_python_canary` halves consult a
  declared `result_report`, or be explicitly excluded?
- **D-3.** Blocker 4. Should the overridden exit code become a verdict field?
  (Schema change — out of Checkpoint 1 scope as written; naming it so it is a
  decision rather than a silent omission.)

---

## What I verified as CORRECT — do not re-open in the fix round

These were checked independently and hold up.

1. **SR-6's binding constraints are all respected.** `git diff --name-only`
   shows nothing outside `assay/`. `src/assay/errors.py`, `src/assay/verdict.py`
   and `src/assay/schemas/verdict.schema.json` are not in the diff at all — so
   `Outcome`/`EXIT_CODES` (A-021) and the `ReasonCode` enum (A-050) are
   untouched, and no `run-gate.py` or dstdns-side change is present. No retry
   heuristic and no CPU-quota primitive anywhere in the diff.
2. **Checkpoints 2 and 3 are genuinely not built**, and it is enforced, not
   just claimed: `RESULT_REPORT_FORMATS` is derived from the `_READERS`
   registry rather than hand-listed, and
   `test_result_reports.py:171` asserts
   `RESULT_REPORT_FORMATS == frozenset({"vitest-json"})`. Declaring
   `pytest-json-report` is refused at config load, and
   `test_an_unregistered_format_refuses_rather_than_guessing` covers the
   reader side too.
3. **A-073's default is byte-identical for an undeclaring lane.** With
   `summary is None`, `runner.py:1213` reduces to `passed = proc.returncode == 0`,
   the green arm at 1215-1222 is field-for-field the pre-change happy path
   (tails omitted), and the non-green arm yields `FAIL`/`COMMAND_FAILED` with
   the same four tail fields. The `execute_plan`/`_execute_plan_inner` split is
   a pure refactor for reservation lifetime. Confirmed empirically:
   `tests/test_runner_run_lane.py`, `test_self_hosting.py` and the canary
   suites are 196 passed / 1 skipped on the branch.
4. **`Claim._check_detail` really does forbid `detail` on a PASS** —
   `src/assay/verdict.py:3318-3322` raises
   *"a PASS carries no detail — detail is the refusing sentence, and a pass
   refused nothing (A-428)"*. SR-6's suggested free-text-`detail` provenance
   route is therefore structurally impossible in the exact direction that
   needed annotating. The implementer's decision to drop it is correct and
   well-reasoned; the only problem is the inaccurate description of what
   replaced it (Blocker 4).
5. **The RecursionError guard is a real fix with a real fault-injection
   test**, matching the B072/B075 lineage. `result_reports/vitest_json.py`
   catches `(json.JSONDecodeError, RecursionError)` in one clause;
   `test_a_pathologically_nested_document_refuses_instead_of_crashing`
   (`test_result_reports.py:132`) drives an actual 200,000-deep document, not
   a mock. The estate sweep `test_untrusted_json_parse_sweep.py` covers the
   new module automatically (`SOURCE_ROOT.rglob("*.py")`, line 179), which is
   how it was caught — a genuine mechanical guard, not a claim.
6. **The `test_self_hosting.py` mutation repin is real and preserves the
   test's meaning.** `_MUTATION_OLD` now targets the conditional pair
   `outcome=Outcome.PASS if passed else Outcome.FAIL` /
   `reason_code=None if passed else ReasonCode.COMMAND_FAILED`, collapsing to
   the same universal-PASS bug the original text described. Still a unique,
   one-line-surgical edit; suite green.
7. **Design decision (a) — TOML shape — is sound and well documented.**
   `[lanes.<name>.result_report]` with both `format` and `path` required,
   validated like every other lane sub-table (table check, unknown-key check,
   required-key check, closed vocabulary, path grammar via
   `_validate_omission_path`). The cwd-relative resolution is stated three
   times in `CONSUMERS.md` (bolded), once in `DESIGN-GUIDE.md`, and once in the
   `ResultReportConfig.path` field docstring, each giving the *reason*
   (`--outputFile` resolves against it). A consumer would not get this wrong.
8. **Design decision (b) — module location — is what was claimed.**
   `src/assay/result_reports/` is `__init__.py` (registry + `read_verified`) +
   `model.py` (normalized types + the format-agnostic `verify_complete`) +
   `vitest_json.py` (one format), which is structurally the
   `coverage_parsers/`/`mutation_parsers/` shape, not a one-off. The
   `__init__.py` docstring argues correctly why `adapters/` (the *language*
   registry, with a five-method protocol) was the wrong home.
9. **The reservation discipline is an addition beyond the design's ask, and a
   good one.** `_reserve_result_report` (`runner.py:981`) reserves and arms
   before launch, so a stale report from an earlier run — the exact artifact a
   crash leaves behind at the declared path — cannot be read as evidence of a
   run that never happened. This closes a hole SR-2 did not think of. Every
   failure returns `None` (fall back to A-073), never an ERROR terminal, which
   is the correct asymmetry.

### Hollow-test probes (mutation of the implementation)

Three mutations applied to the branch and reverted; the tree was verified
clean afterwards.

| Mutation | Result |
|---|---|
| `verify_complete`: `if not summary.finished:` → `if False:` | **killed**, 1 test (`test_the_core_refuses_every_incomplete_shape[unfinished]`) |
| `runner.py:1213`: `summary.failed == 0` → `proc.returncode == 0 or summary.failed == 0` (the one-directional escape hatch SR-2 forbids) | **killed**, 2 tests |
| `_reserve_result_report`: drop the `reservation.arm()` block | **killed**, 33 tests |

The tests are load-bearing, not decorative. The RG-45 reproduction test and
the stale-report test in particular both go red under targeted mutation.

---

## Non-blocking observations

- **OBS 1.** `verify_complete`'s `finished` check is dead code for the only
  shipped format: `vitest_json.read` raises `ReportUnusable` itself when
  `success` is absent, so a `ReportSummary(finished=False)` is unreachable
  today. The mutation probe confirms it — disabling the core check failed only
  the direct unit test, not one runner-level fault-injection case. This is
  fine (correct defence-in-depth for Checkpoints 2/3, and the split is the
  right design), but worth knowing that the runner-level matrix does not
  exercise that bullet.
- **OBS 2.** The new reader was not added to
  `test_untrusted_json_parse_sweep.py`'s pinned list at line 238-249 (still
  eight entries; its docstring still says "These eight"). The derived sweep
  covers it while it exists, but the pinned list exists precisely because the
  sweep cannot notice a site that *disappears*, and the vitest reader is
  squarely a "reads bytes assay did not write" site. One line to add.
- **OBS 3 (answers the prompt's item 6).** The silent fallback on a
  not-yet-existing parent directory is **acceptable as designed**, and better
  founded than the implementer claimed: `safeio.reserve_output`'s
  `create_missing_parents=False` default is documented at
  `src/assay/safeio.py:423-428` as "the CONTRACT, not an implementation
  accident", opt-in only for a caller that owns an ephemeral snapshot. The
  direct R0 path measures the consumer's live tree, so `False` is correct
  there. It is clearly documented in `CONSUMERS.md` ("Declare a path whose
  directory already exists") with the reason and a worked always-works
  example. I would **not** block on it. I would note that a lane author who
  mistypes a directory gets zero signal, and that `--progress`'s
  `command_finished` event would be a natural, schema-free place to say "a
  declared report was not usable" — worth considering in Checkpoint 2 rather
  than now. Once Blocker 1 is fixed, the snapshot half can legitimately pass
  `create_missing_parents=True` and the limitation narrows to the direct path
  (D-1).
- **OBS 4.** After this change, "a `PASS` verdict that carries
  `result_stdout_tail`" becomes the only way to detect that a report overrode
  a non-zero exit. That is a real and reasonably discoverable signal, but it is
  implicit and currently undocumented as a detection method. If Blocker 4 is
  fixed by documenting the tails rather than adding a field, say this
  explicitly in `CONSUMERS.md`.
- **OBS 5.** `DESIGN-GUIDE.md:572` ("The tiebreak applies to the lane's own R0
  command alone") reads as a completeness statement and is currently false in
  both directions — it under-delivers for R1+ lanes (Blocker 1) and
  over-delivers for legacy canary halves (Blocker 3). Re-word once both are
  settled.

---

## Frontmatter / acceptance-box reconciliation

Checked against `4-backlog.md:8120-8141`, not the REPORT's paraphrase.

| Acceptance item | Claimed | Actual |
|---|---|---|
| verified-complete + 0 failures + non-zero exit → `PASS` | shipped | **Partial** — true on the direct R0-only path; false for R1+ lanes, i.e. false for the cited live repro (Blocker 1) |
| truncated/malformed/absent → A-073 unchanged | shipped | **True**, well covered, mutation-verified |
| verified-complete + real failures → `FAIL` regardless of exit | shipped | **True**, mutation-verified in both directions |
| lane not declaring → byte-for-byte unaffected | shipped | **True** for lane behaviour; but see Blocker 3 — a lane that *does* declare reaches a path the branch says it does not |
| fault-injection tests for every completeness-failure shape | shipped | **True** — twelve unusable shapes in both exit-code directions, plus stale/symlink/never-written |

The backlog's Checkpoint 1 box (`4-backlog.md:8122`) and item 2
(`4-backlog.md:8137`) are both already ticked `[x]` **in this branch's own
diff**. Given Blocker 1, the Checkpoint 1 tick is premature and should be
reverted to `[ ]` until the R1+ path is wired; the fault-injection tick is
earned and can stand.

---

## Summary for the controller

The engineering is good and most of it should survive the fix round
unchanged. The implementer deserves credit for catching that the design's own
wiring pointer was a docstring and that `execute_command` is not on the
shipped path — that correction is right, and moving the rule into
`execute_plan` is the right structure. But the reasoning they used to justify
it rests on a false fact about `ui_unit`'s declared rigor, and following that
false fact led them to wire the one R0 path that RG-45's actual lane never
takes. The result is a green branch that does not fix the bug it was written
for. Fix Blocker 1 (plus the three smaller accuracy/invariant blockers),
re-run, and this should land.
